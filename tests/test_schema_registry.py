"""The schema registry, kept honest against the tree it describes.

A registry is only worth something if it cannot rot. Three things rot it: a
migration adds a table nobody classifies, a refactor moves the statement a
citation points at, and a hand-written status drifts away from what the code
actually does. Each one gets a test:

* **coverage** — ``alembic upgrade head`` in a temp directory is the live
  schema, and its table set must equal the registry's keys. A migration that
  creates a table without an entry fails here, before anyone can run an audit
  off a stale list.
* **grounding** — every ``path:line`` must exist and the line must name its own
  table. A moved statement fails with the citation that went stale, so the
  evidence cannot quietly point at a blank line.
* **agreement** — :func:`app.engine.schema_registry.scan_table_access` re-reads
  the tree with ``ast`` and the status must follow from it: writers make a table
  ``written``, readers alone make it ``read-only``, neither makes it ``dead``.
  The registry may not call a table dead while a writer exists, and it may not
  invent SQL the scanner cannot see (a manual-only claim has to declare an
  ``external_owner``).

The scanner's blind spots are tested too, in the same spirit as
``tests/test_engine_audit.py``: the f-string, the concatenation and the
``%s``-formatted SQL are asserted to be *invisible*, so nobody mistakes the
scanner for a complete proof. The registry's notes are what carry those cases.

Finally the document is checked, not trusted: ``reference/sqlite-tables.md``
must list exactly the registry's tables and statuses, or the two halves of the
deliverable have already disagreed.

Nothing here writes a database outside ``tmp_path``. The production file
``ztockz.db`` is opened ``mode=ro`` (URI) and hashed before and after the module
so a test that mutated it would fail rather than be discovered later.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterator

import pytest

from app.db import ensure_schema
from app.engine.schema_registry import (
    STATUSES,
    TABLES,
    TableAccess,
    scan_table_access,
    scan_table_usage,
    sql_references,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = REPO_ROOT / "reference" / "sqlite-tables.md"
PRODUCTION_DB = REPO_ROOT / "ztockz.db"

#: A document row: ``| `table` | status | ... |``.
_TABLE_ROW = re.compile(
    r"^\|\s*`(?P<table>[a-z_][a-z0-9_]*)`\s*\|\s*(?P<status>[a-z-]+)\s*\|", re.MULTILINE
)
_REGISTRY_BLOCK = re.compile(r"<!-- registry:start -->(.*?)<!-- registry:end -->", re.DOTALL)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _database_files() -> dict[str, str]:
    """Hash the database itself; the WAL sidecars are excluded on purpose.

    SQLite creates and removes ``-wal``/``-shm`` whenever *any* process opens
    the file, and the running service opens this one constantly. A check that
    included them would fail exactly while the deployment is healthy — it would
    be measuring the service's activity, not this module's writes.
    """

    path = REPO_ROOT / "ztockz.db"
    return {path.name: _sha256(path)} if path.is_file() else {}


@pytest.fixture(scope="module", autouse=True)
def _production_database_untouched() -> Iterator[None]:
    """Fail the module if anything here rewrote ``ztockz.db`` or its WAL."""

    before = _database_files()
    yield
    after = _database_files()
    assert after == before, (
        "a test in this module changed the production database files: "
        f"{sorted(name for name in before if before.get(name) != after.get(name))}"
    )


@pytest.fixture(scope="module")
def access() -> dict[str, TableAccess]:
    return scan_table_access(REPO_ROOT)


@pytest.fixture(scope="module")
def usage() -> dict[str, set[str]]:
    return scan_table_usage(REPO_ROOT)


def _schema_tables(db_path: Path) -> set[str]:
    """The tables ``alembic upgrade head`` creates, read back from SQLite."""

    ensure_schema(db_path)
    with closing(sqlite3.connect(db_path)) as connection:
        return {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }


# ---------------------------------------------------------------------------
# Coverage: the registry is the schema, no more and no less
# ---------------------------------------------------------------------------


def test_every_table_in_the_schema_is_classified(tmp_path: Path) -> None:
    """A migration that adds a table without a registry entry fails here.

    The schema is built from the migrations rather than from ``ztockz.db`` so
    this gate runs on a clean checkout and in CI, where the production file may
    not exist. ``tmp_path`` keeps Alembic off the repository entirely.
    """

    migrated = _schema_tables(tmp_path / "schema.db")
    classified = set(TABLES)

    assert migrated == classified, (
        "reference/sqlite-tables.md and app/engine/schema_registry.py are missing "
        f"tables the migrations create: {sorted(migrated - classified)}; "
        f"the registry names tables the schema does not have: {sorted(classified - migrated)}"
    )


def test_registry_keys_match_the_declared_names() -> None:
    """``TABLES[name].name`` must be ``name`` — one fact, stored once."""

    for key, usage in TABLES.items():
        assert usage.name == key, f"{key!r} declares itself as {usage.name!r}"


def test_the_registry_is_immutable() -> None:
    """A mutable mapping would let a test or a route rewrite the audit trail."""

    with pytest.raises(TypeError):
        TABLES["observations"] = TABLES["observations"]  # type: ignore[index]


def test_status_vocabulary_is_closed() -> None:
    """Four statuses, no synonyms: ``dead`` must not mean three different things."""

    for usage in TABLES.values():
        assert usage.status in STATUSES, f"{usage.name}: unknown status {usage.status!r}"


def test_every_entry_has_a_note_and_evidence() -> None:
    """An entry without a note is a label, and a label is what drifted before."""

    for usage in TABLES.values():
        assert usage.evidence, f"{usage.name}: no evidence"
        assert usage.note.endswith("."), f"{usage.name}: note is not a sentence"
        assert "\n" not in usage.note, f"{usage.name}: note must be one sentence, one line"
        for item in usage.evidence:
            assert re.fullmatch(r"[^:]+\.py:\d+", item), f"{usage.name}: bad citation {item!r}"


# ---------------------------------------------------------------------------
# Grounding: every citation is a real line that names its own table
# ---------------------------------------------------------------------------


def test_evidence_points_at_a_real_line_that_names_the_table() -> None:
    """A moved statement must fail here, not in the next audit.

    This is the check that keeps the evidence worth reading: a line number that
    survives a refactor by accident (same offset, different statement) is
    caught because the line text has to mention the table.
    """

    for usage in TABLES.values():
        for item in usage.evidence:
            path_text, _, line_text = item.rpartition(":")
            path = REPO_ROOT / path_text
            assert path.is_file(), f"{usage.name}: evidence file {path_text} is gone"

            lines = path.read_text(encoding="utf-8").splitlines()
            line_number = int(line_text)
            assert 1 <= line_number <= len(lines), (
                f"{usage.name}: {item} is past the end of {path_text} ({len(lines)} lines)"
            )
            assert usage.name in lines[line_number - 1], (
                f"{usage.name}: {item} does not mention the table — {lines[line_number - 1]!r}"
            )


def test_registry_evidence_overlaps_the_scanner(access: dict[str, TableAccess]) -> None:
    """Every app-owned table's citations must land in files the scanner also sees.

    ``dead`` tables are the exception that proves the rule: their evidence is the
    model declaration nobody imports, so the assertion inverts — the scanner must
    see *nothing* for them.
    """

    for usage in TABLES.values():
        if usage.external_owner is not None:
            continue
        if usage.status == "dead":
            assert usage.name not in access, (
                f"{usage.name} is declared dead but {sorted(access[usage.name].files)} touch it"
            )
            continue
        seen = access.get(usage.name)
        assert seen is not None, f"{usage.name}: the scanner sees no SQL for it"

        cited = {item.rpartition(":")[0] for item in usage.evidence}
        assert cited & set(seen.files), (
            f"{usage.name}: no citation is in a file the scanner found "
            f"(cited={sorted(cited)}, seen={sorted(seen.files)})"
        )


# ---------------------------------------------------------------------------
# Agreement: the declared status follows from the scanner
# ---------------------------------------------------------------------------


def test_status_follows_the_scanner(access: dict[str, TableAccess]) -> None:
    """``written``/``read-only``/``dead`` are derived, not chosen.

    A table whose only writes are invisible to the scanner (dynamic SQL) cannot
    be declared ``written`` here without also declaring why its owner is outside
    ``app/``. That is deliberate: the alternative is a registry nobody can
    check, which is the state this file exists to end.
    """

    for usage in TABLES.values():
        seen = access.get(usage.name)
        if usage.external_owner is not None:
            assert usage.status in {"read-only", "reserved"}, (
                f"{usage.name}: an externally owned table cannot be {usage.status!r}"
            )
            assert seen is None, (
                f"{usage.name}: declares an external owner but app/ SQL names it — "
                "drop external_owner and let the scanner prove the status"
            )
            continue

        if seen is None:
            expected = "dead"
        elif seen.writers:
            expected = "written"
        else:
            expected = "read-only"
        assert usage.status == expected, (
            f"{usage.name}: registry says {usage.status!r}, the scanner says {expected!r} "
            f"(writers={sorted(seen.writers) if seen else []}, "
            f"readers={sorted(seen.readers) if seen else []})"
        )


def test_no_table_called_dead_is_touched_by_sql(usage: dict[str, set[str]]) -> None:
    """The direct form of the rule: dead means nobody reads *and* nobody writes."""

    for name, declared in TABLES.items():
        if declared.status != "dead":
            continue
        assert name not in usage, (
            f"{name} is declared dead but {sorted(usage[name])} still touch it"
        )


def test_dead_set_is_exactly_the_unreferenced_tables(usage: dict[str, set[str]]) -> None:
    """Dead must be neither too small (hiding code) nor too large (slandering it)."""

    dead = {name for name, usage_ in TABLES.items() if usage_.status == "dead"}
    unreferenced = {
        name
        for name, usage_ in TABLES.items()
        if usage_.external_owner is None and name not in usage
    }

    assert dead == unreferenced, (
        f"declared dead={sorted(dead)} but unreferenced={sorted(unreferenced)}"
    )


def test_every_written_table_has_a_writer_file(access: dict[str, TableAccess]) -> None:
    """``written`` without a writer is a label someone typed, not a fact."""

    for usage in TABLES.values():
        if usage.status != "written":
            continue
        seen = access.get(usage.name)
        assert seen is not None and seen.writers, f"{usage.name}: no INSERT/UPDATE/DELETE found"


def test_scanner_finds_no_table_outside_the_registry(usage: dict[str, set[str]]) -> None:
    """SQL against a table the registry does not know is drift in both directions.

    A typo (``jbos``) or a new table whose migration landed without an entry
    shows up here with the file that mentions it.
    """

    unknown = sorted(set(usage) - set(TABLES))
    assert not unknown, f"SQL names tables the registry does not classify: {unknown}"


# ---------------------------------------------------------------------------
# The scanner's contract: what it reads, and what it cannot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        # The four verbs, on the real tables this tree uses.
        ("INSERT INTO jobs (id, status) VALUES (:id, 'Pending')", [("jobs", "INTO", True)]),
        ("UPDATE jobs SET status = 'Done' WHERE id = :id", [("jobs", "UPDATE", True)]),
        ("DELETE FROM jobs WHERE id = :id", [("jobs", "FROM", True)]),
        ("SELECT * FROM jobs WHERE id = :id", [("jobs", "FROM", False)]),
        # Quoted identifiers and leading whitespace (triple-quoted constants).
        (
            '\n            SELECT * FROM "quotes" WHERE id = :id\n        ',
            [("quotes", "FROM", False)],
        ),
        # An upsert is one writer and no phantom reader.
        (
            "INSERT INTO screen_facts (symbol, fields) VALUES (:s, '{}')\n"
            "ON CONFLICT(symbol) DO UPDATE SET fields = excluded.fields",
            [("screen_facts", "INTO", True)],
        ),
        # A write that reads from another table: both directions, separately.
        (
            "INSERT INTO jobs (id) SELECT id FROM observations",
            [("jobs", "INTO", True), ("observations", "FROM", False)],
        ),
        # UPDATE ... WHERE id IN (SELECT ... FROM same table) is a writer to that table.
        (
            "UPDATE jobs SET status = 'Processing'\n"
            " WHERE id IN (SELECT id FROM jobs WHERE status = 'Pending')",
            [("jobs", "UPDATE", True), ("jobs", "FROM", False)],
        ),
        # Several statements in one literal.
        (
            "DELETE FROM screen_rows WHERE screen_id = :id;\nDELETE FROM screens WHERE id = :id",
            [("screen_rows", "FROM", True), ("screens", "FROM", True)],
        ),
        # ``UPDATE OR IGNORE`` names the table after the conflict clause.
        ("UPDATE OR IGNORE quotes SET status = 'Used'", [("quotes", "UPDATE", True)]),
        # A schema probe is not a read: it asks whether jobs exists, not what it holds.
        ("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'", []),
        # Prose that happens to contain a verb is not SQL.
        ("realtime quotes come from internal sources that do not permit resale", []),
        # A near-miss keyword is not a statement.
        ("SELEC * FROM jobs", []),
        # SQL whose first token is a comment: documented blind spot.
        ("-- purge old jobs\nDELETE FROM jobs WHERE expires_at <= :now", []),
    ],
)
def test_the_scanner_reads_the_statement_shapes_this_tree_uses(
    sql: str, expected: list[tuple[str, str, bool]]
) -> None:
    """Pin the extractor's rules on the shapes the repository actually writes."""

    found = [(ref.table, ref.keyword, ref.writes) for ref in sql_references(sql)]
    assert found == expected


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("DELETE FROM {table} WHERE job_id = :job_id", id="fstring_placeholder"),
        pytest.param("SELECT * FROM %s WHERE id = :id", id="percent_placeholder"),
        # the constant half of ``"SELECT * FROM " + name``: it names nothing alone
        pytest.param("SELECT * FROM ", id="concatenated_prefix"),
        pytest.param("PRAGMA table_info(jobs)", id="pragma_probe"),
        pytest.param("SELECT 1", id="no_table"),
        pytest.param("", id="empty"),
    ],
)
def test_the_scanner_cannot_see_these_shapes(sql: str) -> None:
    """The blind spots are facts, not regrets: assert them so nobody forgets.

    ``DELETE FROM {table}`` is the exact text of the retention purge in
    ``app/jobs/repository.py`` (the table names come from a literal tuple in the
    enclosing loop). The registry compensates by citing the literal statements
    that prove each of those three tables is written, so no status rests on the
    invisible purge.
    """

    assert sql_references(sql) == ()


def test_scanner_walks_imported_constants_but_not_computed_sql(tmp_path: Path) -> None:
    """The AST walk sees a module constant, and does not guess at a runtime value.

    This is the shape ``app/resolve/seed.py`` uses (``_UPSERT_INSTRUMENT =
    text(...)`` at module level): a scanner that only looked inside
    ``execute(...)`` calls would report ``instruments`` as dead.
    """

    source = tmp_path / "app" / "sample" / "queries.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            [
                "UPSERT = text('INSERT INTO instruments (instrument_id) VALUES (:id)')",
                "READ = 'SELECT * FROM jobs WHERE id = :id'",
                "",
                "def purge(table):",
                "    return f'DELETE FROM {table} WHERE job_id = :job_id'",
                "",
                "def dynamic(name):",
                "    return 'SELECT * FROM ' + name",
            ]
        ),
        encoding="utf-8",
    )

    access = scan_table_access(tmp_path)

    assert set(access) == {"instruments", "jobs"}
    assert access["instruments"].writers == {"app/sample/queries.py"}
    assert access["jobs"].readers == {"app/sample/queries.py"}
    assert access["jobs"].writers == set()
    assert access["jobs"].files == {"app/sample/queries.py"}


def test_scan_table_usage_is_the_union_of_both_directions(access: dict[str, TableAccess]) -> None:
    """The contracted return shape (``dict[str, set[str]]``) loses no file."""

    usage = scan_table_usage(REPO_ROOT)
    assert set(usage) == set(access)
    for table, files in usage.items():
        assert files == set(access[table].files), table


# ---------------------------------------------------------------------------
# The document cannot disagree with the registry
# ---------------------------------------------------------------------------


def test_document_lists_exactly_the_registry() -> None:
    """``reference/sqlite-tables.md`` is generated from the registry; diff it.

    The block markers exist so this parses the table and not the prose around
    it: a row added by hand without an entry (or an entry without a row) fails.
    """

    block = _REGISTRY_BLOCK.search(DOCUMENT.read_text(encoding="utf-8"))
    assert block is not None, "reference/sqlite-tables.md lost its registry block markers"

    rows = dict(_TABLE_ROW.findall(block.group(1)))
    assert rows == {name: usage.status for name, usage in TABLES.items()}, (
        "the document and the registry disagree; regenerate the block from "
        "app.engine.schema_registry.TABLES"
    )


def test_document_explains_every_status_it_uses() -> None:
    """A reader who has to guess what ``dead`` means will guess wrong."""

    body = DOCUMENT.read_text(encoding="utf-8")
    for status in sorted({usage.status for usage in TABLES.values()}):
        assert f"`{status}`" in body, f"the document never defines {status!r}"


# ---------------------------------------------------------------------------
# The production database: read it, never touch it
# ---------------------------------------------------------------------------


def test_read_only_connection_cannot_mutate_a_database(tmp_path: Path) -> None:
    """``mode=ro`` is the promise; this proves it against a freshly migrated file."""

    db_path = tmp_path / "ro.db"
    ensure_schema(db_path)
    before = _sha256(db_path)
    files_before = {path.name for path in tmp_path.iterdir()}

    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone() == (0,)
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO jobs (id) VALUES ('x')")

    assert _sha256(db_path) == before, "a read-only connection changed the database file"
    assert {path.name for path in tmp_path.iterdir()} == files_before, (
        "a read-only connection left a journal/WAL file behind"
    )


def test_production_schema_is_covered_by_the_registry() -> None:
    """The measured file, not just the migrations: 14 tables, all classified.

    Skipped on a checkout without ``ztockz.db``. The module-level autouse
    fixture hashes this file before and after the module, so the read is proven
    read-only by the rest of the run.
    """

    if not PRODUCTION_DB.is_file():
        pytest.skip("ztockz.db is not present in this checkout")

    uri = f"file:{PRODUCTION_DB}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        live = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert live <= set(TABLES), (
        f"the live database has unclassified tables: {sorted(live - set(TABLES))}"
    )
    assert len(live) == len(TABLES), (
        f"the live database has {len(live)} tables, the registry {len(TABLES)}; "
        "a discrepancy belongs in reference/sqlite-tables.md before it is fixed"
    )
