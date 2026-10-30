"""The silent-catch audit: a table of snippets first, then the real tree.

The tree assertion is only worth something if the detector can tell a swallow
from a fallback, so the snippet table is the primary test: eighteen shapes that
must be reported, sixteen legitimate shapes that must not (broad catches that
re-raise, log then fall back, or hand the exception to a recorder). The tree
tests then pin the *current* number of accepted silences, so a new one is a
visible regression rather than a line someone reads past.

Everything here is static: the module under test parses source, it never
imports the tree and never touches the network (``conftest.py`` enforces the
second part for the whole suite).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.engine import audit

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"
ALLOWLIST = APP_ROOT / "engine" / "audit_allowlist.txt"

# ---------------------------------------------------------------------------
# Snippets: the detector's own contract
# ---------------------------------------------------------------------------

#: ``(name, body, expected kind)`` — every one of these must be reported.
SILENT_SNIPPETS = [
    ("pass", "try:\n    work()\nexcept Exception:\n    pass\n", "noop"),
    ("continue", "for x in xs:\n    try:\n        work(x)\n    except Exception:\n        continue\n", "noop"),
    ("break", "while True:\n    try:\n        work()\n    except Exception:\n        break\n", "noop"),
    ("ellipsis", "try:\n    work()\nexcept Exception:\n    ...\n", "noop"),
    ("bare_colon", "try:\n    work()\nexcept:\n    pass\n", "noop"),
    ("base_exception", "try:\n    work()\nexcept BaseException:\n    pass\n", "noop"),
    ("return_dict", "try:\n    work()\nexcept Exception:\n    return {}\n", "empty-return"),
    ("return_list", "try:\n    work()\nexcept Exception:\n    return []\n", "empty-return"),
    (
        "return_empty_pair",
        "try:\n    work()\nexcept Exception:\n    return {}, {}\n",
        "empty-return",
    ),
    ("return_none", "try:\n    work()\nexcept Exception:\n    return None\n", "empty-return"),
    ("return_empty_str", "try:\n    work()\nexcept Exception:\n    return ''\n", "empty-return"),
    ("return_empty_ctor", "try:\n    work()\nexcept Exception:\n    return dict()\n", "empty-return"),
    ("unused_alias", "try:\n    work()\nexcept Exception as exc:\n    return {}\n", "empty-return"),
    ("tuple_catch", "try:\n    work()\nexcept (Exception, ValueError):\n    return []\n", "empty-return"),
    ("local_none", "try:\n    spec = parse(name)\nexcept Exception:\n    spec = None\n", "discarded"),
    ("return_false", "try:\n    work()\nexcept Exception:\n    return False\n", "discarded"),
    # stdout chatter is not telemetry: a print with no explicit stream reports
    # to nobody, so it must be reported even though it looks like logging.
    ("print_to_stdout", "try:\n    work()\nexcept Exception as exc:\n    print(f'boom {exc}')\n", "discarded"),
    # computing a string and dropping it is still a swallow.
    ("discarded_str", "try:\n    work()\nexcept Exception as exc:\n    detail = str(exc)\n", "discarded"),
]

#: Legitimate shapes. A detector that reports any of these is noise.
CLEAN_SNIPPETS = [
    ("bare_raise", "try:\n    work()\nexcept Exception:\n    raise\n"),
    ("raise_new", "try:\n    work()\nexcept Exception as exc:\n    raise RuntimeError('boom') from exc\n"),
    ("logger_warning", "try:\n    work()\nexcept Exception as exc:\n    logger.warning('x failed: %s', exc)\n"),
    ("object_logger", "try:\n    work()\nexcept Exception as exc:\n    self.log.error('x failed: %s', exc)\n"),
    ("logger_exception_then_fallback", "try:\n    work()\nexcept Exception:\n    logger.exception('boom')\n    return {}\n"),
    ("record_then_empty", "try:\n    work()\nexcept Exception as exc:\n    self._record(sym, exc)\n    return {}\n"),
    ("return_diagnostic", "try:\n    work()\nexcept Exception as exc:\n    return {'error': str(exc)}\n"),
    ("counter", "try:\n    work()\nexcept Exception:\n    failed += 1\n"),
    ("append_ledger", "try:\n    work()\nexcept Exception as exc:\n    failures.append(exc)\n"),
    ("job_fail", "try:\n    work()\nexcept Exception as exc:\n    await jobs.fail(job_id, error=str(exc), retryable=False)\n"),
    ("factory_report", "try:\n    work()\nexcept Exception as exc:\n    pool.report_failure(proxy, FREE)\n"),
    ("stderr_print", "try:\n    work()\nexcept Exception as exc:\n    print('boom', file=sys.stderr)\n"),
    ("stored_reason", "try:\n    work()\nexcept Exception as exc:\n    self.build_error = f'{type(exc).__name__}: {exc}'\n"),
    ("verdict_object", "try:\n    work()\nexcept Exception as exc:\n    verdict = Verdict(False, 'schema_drift', f'raised {type(exc).__name__}')\n"),
    # A narrow catch names the failure it expects; out of scope by default.
    ("narrow_handler", "try:\n    value = float(cell)\nexcept ValueError:\n    return None\n"),
    ("no_handler", "try:\n    work()\nfinally:\n    done()\n"),
]


@pytest.mark.parametrize(
    ("name", "body", "kind"),
    SILENT_SNIPPETS,
    ids=[row[0] for row in SILENT_SNIPPETS],
)
def test_silent_shapes_are_reported(name: str, body: str, kind: str) -> None:
    findings = audit.find_silent_handlers(body, "snippet.py")

    assert len(findings) == 1, f"{name}: expected exactly one finding, got {findings}"
    assert findings[0].kind == kind
    assert findings[0].path == "snippet.py"
    assert findings[0].lineno == body.count("\n", 0, body.index("except")) + 1


@pytest.mark.parametrize(
    ("name", "body"),
    CLEAN_SNIPPETS,
    ids=[row[0] for row in CLEAN_SNIPPETS],
)
def test_legitimate_shapes_are_clean(name: str, body: str) -> None:
    assert audit.find_silent_handlers(body, "snippet.py") == [], name


def test_the_alias_form_does_not_hide_a_pass() -> None:
    body = "try:\n    work()\nexcept Exception as exc:\n    pass\n"

    assert len(audit.find_silent_handlers(body, "snippet.py")) == 1


def test_a_nested_try_inside_the_handler_is_still_the_handler() -> None:
    """A handler that swallows twice is one finding, not zero."""

    body = (
        "try:\n"
        "    work()\n"
        "except Exception:\n"
        "    try:\n"
        "        cleanup()\n"
        "    except Exception:\n"
        "        pass\n"
    )

    findings = audit.find_silent_handlers(body, "snippet.py")

    assert [f.lineno for f in findings] == [3, 6]


def test_broad_only_false_reports_the_narrow_swallow() -> None:
    body = "try:\n    value = float(cell)\nexcept ValueError:\n    pass\n"

    assert audit.find_silent_handlers(body, "snippet.py") == []
    findings = audit.find_silent_handlers(body, "snippet.py", broad_only=False)

    assert len(findings) == 1
    assert findings[0].handler == "ValueError"


def test_finding_string_names_the_location_and_the_reason() -> None:
    finding = audit.find_silent_handlers(
        "try:\n    work()\nexcept Exception:\n    pass\n", "app/x.py"
    )[0]

    assert str(finding).startswith("app/x.py:3: noop: ")


# ---------------------------------------------------------------------------
# Adversarial: what the detector catches, and what it provably cannot
# ---------------------------------------------------------------------------


def test_a_noop_helper_call_is_still_silent() -> None:
    """``except: self._noop()`` is a swallow with a longer body."""

    body = "try:\n    work()\nexcept Exception:\n    self._noop()\n"

    assert len(audit.find_silent_handlers(body, "snippet.py")) == 1


def test_a_handler_hidden_in_a_nested_function_is_still_found() -> None:
    """The scan walks the whole module, not just module-level statements."""

    body = (
        "def outer():\n"
        "    def inner():\n"
        "        try:\n"
        "            work()\n"
        "        except Exception:\n"
        "            pass\n"
        "    return inner\n"
    )

    assert len(audit.find_silent_handlers(body, "snippet.py")) == 1


# -- Known limits: pinned so nobody mistakes the audit for a proof -----------
#
# Each of these is a real way to swallow without being reported. They are
# asserted (not merely described) so that a future improvement to the detector
# makes the test fail and forces the documentation to be updated.


def test_known_limit_a_call_that_takes_the_exception_launders_the_handler() -> None:
    """A helper that takes the exception counts as "kept" even if it drops it."""

    body = "try:\n    work()\nexcept Exception as exc:\n    self._swallow(exc)\n"

    assert audit.find_silent_handlers(body, "snippet.py") == []  # MISS


def test_known_limit_contextlib_suppress_has_no_except_node() -> None:
    """``contextlib.suppress`` swallows by design and is invisible to an AST walk."""

    body = "with contextlib.suppress(Exception):\n    work()\n"

    assert audit.find_silent_handlers(body, "snippet.py") == []  # MISS


def test_known_limit_an_alias_of_exception_is_not_recognised() -> None:
    """Broadness is decided by name, because the audit never imports the tree."""

    body = "EXC = Exception\ntry:\n    work()\nexcept EXC:\n    pass\n"

    assert audit.find_silent_handlers(body, "snippet.py") == []  # MISS


def test_known_limit_a_debug_log_counts_as_a_signal() -> None:
    """The level is a policy question; the audit only enforces structure."""

    body = "try:\n    work()\nexcept Exception as exc:\n    logger.debug('x: %s', exc)\n"

    assert audit.find_silent_handlers(body, "snippet.py") == []  # accepted


# ---------------------------------------------------------------------------
# The tree: pinned count + allowlist discipline
# ---------------------------------------------------------------------------

CURRENT_SILENT_COUNT = 11


def test_the_tree_scan_finds_the_pinned_number_of_silent_handlers() -> None:
    findings = audit.scan_tree(APP_ROOT)

    assert len(findings) == CURRENT_SILENT_COUNT, "\n".join(str(f) for f in findings)


def test_every_silent_handler_is_in_the_allowlist() -> None:
    allowed = audit.load_allowlist(ALLOWLIST)
    unmatched, _ = audit.match_allowlist(audit.scan_tree(APP_ROOT), allowed)

    assert not unmatched, (
        "silent broad handler(s) with no entry in app/engine/audit_allowlist.txt:\n"
        + audit.format_findings(unmatched)
    )


def test_allowlist_has_no_stale_entries() -> None:
    allowed = audit.load_allowlist(ALLOWLIST)
    findings = audit.scan_tree(APP_ROOT)
    _, stale = audit.match_allowlist(findings, allowed)

    def _hint(path: str, line: int) -> str:
        nearby = [f for f in findings if f.path == path and abs(f.lineno - line) <= 60]
        if not nearby:
            return " (the handler is gone: delete the entry)"
        return f" (ambiguous: {len(nearby)} live silences on that file)"

    assert not stale, (
        "allowlist entries that no longer match a silent handler "
        "(delete them, or re-pin the line number after a real fix):\n"
        + "\n".join(
            f"{path}:{line}: {allowed[(path, line)]}{_hint(path, line)}"
            for path, line in stale
        )
    )


def test_the_matcher_tolerates_a_handler_moving_down_its_file() -> None:
    """A line inserted above an allowlisted handler must not break the build.

    Sibling edits move lines for reasons unrelated to the silence; the entry
    stays valid as long as exactly one silence moved on that path.
    """

    allowed = {("app/x.py", 10): "known"}
    moved = [audit.Finding("app/x.py", 14, "noop", "Exception", "pass")]

    unmatched, stale = audit.match_allowlist(moved, allowed)

    assert (unmatched, stale) == ([], [])


def test_the_matcher_refuses_to_guess_when_two_silences_moved_on_one_path() -> None:
    allowed = {("app/x.py", 10): "known"}
    moved = [
        audit.Finding("app/x.py", 14, "noop", "Exception", "pass"),
        audit.Finding("app/x.py", 40, "empty-return", "Exception", "return {}"),
    ]

    unmatched, stale = audit.match_allowlist(moved, allowed)

    assert len(unmatched) == 2
    assert stale == [("app/x.py", 10)]


def test_the_matcher_calls_an_entry_stale_when_its_handler_is_gone() -> None:
    allowed = {("app/x.py", 10): "known"}

    unmatched, stale = audit.match_allowlist([], allowed)

    assert unmatched == []
    assert stale == [("app/x.py", 10)]


def test_allowlist_entries_are_well_formed_and_point_at_real_files() -> None:
    entries = audit.load_allowlist(ALLOWLIST)

    assert entries, "the allowlist must hold the accepted silences, not be empty"
    for relpath, line in entries:
        assert (REPO_ROOT / relpath).is_file(), relpath
        assert line > 0


def test_allowlist_matches_the_pinned_count() -> None:
    """The count and the allowlist must move together, or one of them lies."""

    assert len(audit.load_allowlist(ALLOWLIST)) == CURRENT_SILENT_COUNT


# ---------------------------------------------------------------------------
# Proof that a new swallow fails the audit
# ---------------------------------------------------------------------------


def test_a_planted_silent_handler_is_reported_on_a_copy_of_the_tree(
    tmp_path: Path,
) -> None:
    """The tree test's teeth, exercised without touching the repository.

    A copy of ``app/`` is scanned, one file gets a fresh ``except``, and the
    scan must report exactly one more finding at the planted line. This is the
    mechanism the plan asks for; the repository itself is never edited.
    """

    copy = tmp_path / "app"
    shutil.copytree(APP_ROOT, copy, ignore=shutil.ignore_patterns("__pycache__"))
    victim = copy / "market" / "depth.py"
    before = len(audit.scan_tree(copy))

    original = victim.read_text(encoding="utf-8")
    planted = original + (
        "\n\ndef _planted_probe(payload: object) -> dict:\n"
        "    try:\n"
        "        return {'n': int(payload)}\n"
        "    except Exception:\n"
        "        pass\n"
        "    return {}\n"
    )
    victim.write_text(planted, encoding="utf-8")

    findings = audit.scan_tree(copy)
    added = [f for f in findings if f.lineno > original.count("\n")]

    assert len(findings) == before + 1
    assert len(added) == 1, findings
    assert added[0].kind == "noop"
    assert added[0].path.endswith("market/depth.py")
    # ``max``: the appended block is the *last* handler at that indentation.
    expected = max(
        index
        for index, line in enumerate(planted.splitlines(), start=1)
        if line == "    except Exception:"
    )
    assert added[0].lineno == expected
