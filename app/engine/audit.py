"""Static audit: find ``except`` handlers that swallow the failure in silence.

Phase 4 of the error-resilience plan (§6). The plan's measurement was 95
``except Exception`` handlers in ``app/`` against four ``logger.exception``
calls; at the time of writing the tree carries 104 broad catches, of which 11
make their failure invisible. The rule the audit enforces is not "never catch
broadly" — an optional rung *must* catch broadly so one dead source cannot sink
a depth cycle. The rule is that a broad catch may not make the failure
invisible:

    every broad handler must do at least one of
      a. re-raise,
      b. log (a ``debug``/``info``/``warning``/``error``/``exception`` call),
      c. keep the failure: bump a counter, append to a ledger, pass the
         exception to a recorder, or hand it back in a returned value.

A handler that does none of the three is reported.

What counts as a broad catch
----------------------------
``except:``, ``except Exception``, ``except BaseException``, and any tuple
carrying one of them (``except (Exception, ValueError)``). A *narrow* handler
(``except ValueError: return None``) makes a claim about the failure it expects
and its ``None`` is a documented "absent" answer, so it is out of scope by
default; scanning the whole tree with ``broad_only=False`` still reports them
(177 handlers at the time of writing), which is why the tree test keeps the
narrow set out of the count: a detector that flags every ``except`` in a
six-year-old codebase is a detector nobody can act on.

What counts as a signal
-----------------------
* **log** — a call to an attribute named ``debug``/``info``/``warning``/
  ``warn``/``error``/``exception``/``critical``/``fatal``/``log`` on *anything*
  (``logger.exception``, ``self.log.warning``, ``caplog.error``), a call to a
  module-level function with one of those names, or ``print(..., file=...)`` —
  a write to an explicit stream is a report; a bare ``print`` is stdout
  chatter and is **not** a signal.
* **raise** — any ``raise`` statement, bare (re-raise) or carrying a new error.
* **keep** — the exception is not discarded: it reaches a call argument, a
  returned value, or an attribute/subscript being assigned; or the body calls
  something whose name says it records (``record``, ``append``, ``add``,
  ``fail``, ``report_failure``, ``bump``, ``mark``, ...); or it bumps a counter
  (``failed += 1``).

Known limits (documented, not hidden)
------------------------------------
* A ``logger.debug`` is a signal even though production may filter it out; the
  level is a policy question, not a structure question.
* Handlers are parsed, never imported: a class that *subclasses* ``Exception``
  is recognised by name only.
* ``except Exception: print("oops")`` is reported: stdout is not telemetry.
  ``except Exception: print("oops", file=sys.stderr)`` is not, because an
  explicit stream is what a CLI error boundary looks like.
* Signals inside a nested ``def``/``lambda`` are attributed to the enclosing
  handler, because the AST walk does not model deferred execution.
* Line numbers are the allowlist key, so an edit above a known handler would
  otherwise invalidate its entry — a repository with three agents editing it
  would spend its day re-pinning. :func:`match_allowlist` therefore repairs a
  drift when exactly one finding and one entry are left over on the same path;
  it refuses to guess when more than one moved. The pinned count in the test is
  what makes an *extra* silence impossible to hide behind that repair.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

#: The repository root helps the allowlist stay portable: a finding's ``path`` is
#: relative to it whenever the file lives inside it.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Attribute names that mean "this call reports something" — the plan's rule (b).
LOG_ATTRS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log"}
)

#: Module-level functions with the same meaning (``warn(...)``, ``log(...)``).
LOG_FUNCS = frozenset({"log", "warn", "warning", "critical", "fatal"})

#: Call names that mean "this call keeps the failure" — a ledger row, a job
#: failure, a proxy-pool verdict. Leading underscores are stripped before the
#: lookup so ``self._record(...)`` matches.
RECORD_CALLS = frozenset(
    {
        "record",
        "append",
        "add",
        "update",
        "setdefault",
        "incr",
        "increment",
        "fail",
        "report_failure",
        "mark",
        "note",
        "emit",
        "count",
        "bump",
        "track",
        "alert",
        "notify",
        "publish",
        "push",
    }
)

#: Calls that merely *pass the exception through* a conversion or a console
#: write. They must not be mistaken for a recorder, or ``detail = str(exc)``
#: would look like telemetry.
PASSTHROUGH_CALLS = frozenset(
    {"str", "repr", "type", "len", "isinstance", "bool", "int", "float", "format", "print"}
)

#: ``except`` type names that catch everything a rung can throw.
BROAD_TYPES = frozenset({"Exception", "BaseException"})

#: Zero-argument constructors of an empty container.
EMPTY_CTORS = frozenset({"dict", "list", "set", "tuple", "frozenset"})


@dataclass(frozen=True, slots=True)
class Finding:
    """One ``except`` handler that makes its failure invisible."""

    path: str
    lineno: int
    kind: str
    handler: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno}: {self.kind}: {self.reason}"


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _type_names(node: ast.expr | None) -> list[str]:
    """Flatten an ``except`` clause into the names it mentions."""

    if node is None:
        return [""]
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.Tuple):
        return [name for element in node.elts for name in _type_names(element)]
    return [ast.unparse(node)]


def _is_broad(node: ast.expr | None) -> bool:
    if node is None:  # bare ``except:``
        return True
    return any(name in BROAD_TYPES for name in _type_names(node))


def _empty_value(node: ast.expr | None) -> bool:
    """True for the values that carry no information: ``None``, ``{}``, ``[]``."""

    if node is None:
        return True
    if isinstance(node, ast.Constant):
        return node.value is None or node.value == "" or node.value is Ellipsis
    if isinstance(node, ast.Dict):
        return not node.keys
    if isinstance(node, ast.Tuple):
        # ``return {}, {}`` is one "nothing here" from a function with two
        # outputs, so a tuple counts as empty when every element is.
        return not node.elts or all(_empty_value(element) for element in node.elts)
    if isinstance(node, (ast.List, ast.Set)):
        return not node.elts
    if isinstance(node, ast.Call):
        return (
            isinstance(node.func, ast.Name)
            and node.func.id in EMPTY_CTORS
            and not node.args
            and not node.keywords
        )
    return False


def _mentions(node: ast.AST | None, name: str | None) -> bool:
    if node is None or not name:
        return False
    return any(
        isinstance(child, ast.Name) and child.id == name for child in ast.walk(node)
    )


def _own_nodes(body: Sequence[ast.stmt]) -> Iterator[ast.AST]:
    """Walk a handler body without descending into a *nested* handler's body.

    A nested ``try/except`` inside a handler reports its own silence; its
    logging must not launder the outer handler.
    """

    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ExceptHandler):
                continue
            stack.append(child)


def _callee(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _signals(handler: ast.ExceptHandler) -> set[str]:
    """What the handler does with the failure: ``log``, ``raise`` and/or ``keep``."""

    found: set[str] = set()
    exc = handler.name
    for node in _own_nodes(handler.body):
        if isinstance(node, ast.Raise):
            found.add("raise")
        elif isinstance(node, ast.AugAssign):
            # ``failed += 1`` is a counter, which is the plan's rule (c).
            found.add("keep")
        elif isinstance(node, ast.Return):
            if _mentions(node.value, exc):
                found.add("keep")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, (ast.Attribute, ast.Subscript)) and _mentions(
                    node.value, exc
                ):
                    found.add("keep")
        elif isinstance(node, ast.Call):
            name = _callee(node)
            if isinstance(node.func, ast.Attribute) and name in LOG_ATTRS:
                found.add("log")
            elif isinstance(node.func, ast.Name) and name in LOG_FUNCS:
                found.add("log")
            elif isinstance(node.func, ast.Name) and name == "print":
                # ``print(..., file=...)`` writes to a named stream: a CLI
                # error boundary. A bare print is stdout chatter.
                if any(keyword.arg == "file" for keyword in node.keywords):
                    found.add("log")
            if name.lstrip("_") in RECORD_CALLS:
                found.add("keep")
            if name not in PASSTHROUGH_CALLS and exc:
                arguments = list(node.args) + [keyword.value for keyword in node.keywords]
                if any(_mentions(argument, exc) for argument in arguments):
                    found.add("keep")
    return found


def _is_null_statement(statement: ast.stmt) -> bool:
    if isinstance(statement, (ast.Pass, ast.Continue, ast.Break)):
        return True
    # A docstring or a stray ``...`` line carries nothing either.
    return isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)


def _kind(handler: ast.ExceptHandler) -> str:
    body = handler.body
    if all(_is_null_statement(statement) for statement in body):
        return "noop"
    others = [statement for statement in body if not _is_null_statement(statement)]
    if len(others) == 1 and isinstance(others[0], ast.Return):
        if _empty_value(others[0].value):
            return "empty-return"
    return "discarded"


def _describe(handler: ast.ExceptHandler, kind: str) -> str:
    # ``ast.unparse`` is total over a tree that came from ``ast.parse``, so this
    # needs no defensive catch — a swallow here would be the very bug the module
    # reports.
    snippet = "; ".join(ast.unparse(statement) for statement in handler.body[:2])
    snippet = " ".join(snippet.split())[:70]
    if kind == "noop":
        return f'body is only "{snippet}": the exception is discarded without a trace'
    if kind == "empty-return":
        return (
            f'returns the empty value "{snippet}" with no log, no counter and no '
            "re-raise: callers cannot tell 'absent' from 'broken'"
        )
    return (
        f'body does "{snippet}" but never logs, re-raises, counts or returns the '
        "error: the failure is invisible"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_silent_handlers(
    source: str, filename: str, *, broad_only: bool = True
) -> list[Finding]:
    """Every silent handler in ``source``, in line order.

    ``broad_only`` keeps the audit to the catches that make no claim about the
    failure (``except Exception``, ``except:``); ``False`` reports narrow
    swallows too, which is useful when auditing a single module.
    """

    tree = ast.parse(source, filename=filename)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if broad_only and not _is_broad(node.type):
            continue
        if _signals(node):
            continue
        kind = _kind(node)
        findings.append(
            Finding(
                path=filename,
                lineno=node.lineno,
                kind=kind,
                handler=ast.unparse(node.type) if node.type is not None else "bare",
                reason=_describe(node, kind),
            )
        )
    findings.sort(key=lambda finding: (finding.lineno, finding.kind))
    return findings


def _display(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise (a copied tree, a probe)."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def scan_tree(root: str | Path, *, broad_only: bool = True) -> list[Finding]:
    """Every silent handler under ``root`` (recursive ``*.py``), sorted by path."""

    directory = Path(root)
    if not directory.is_dir():
        raise ValueError(f"not a directory: {directory}")
    findings: list[Finding] = []
    for path in sorted(directory.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        findings.extend(
            find_silent_handlers(
                path.read_text(encoding="utf-8"), _display(path), broad_only=broad_only
            )
        )
    findings.sort(key=lambda finding: (finding.path, finding.lineno))
    return findings


def load_allowlist(path: str | Path) -> dict[tuple[str, int], str]:
    """``{(path, lineno): reason}`` from ``path:lineno:reason`` lines.

    Blank lines and ``#`` comments are ignored. A malformed or duplicated entry
    raises: an allowlist that cannot be read exactly is worse than none.
    """

    entries: dict[tuple[str, int], str] = {}
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":", 2)
        if len(parts) != 3 or not parts[2].strip():
            raise ValueError(f"{path}:{number}: expected 'path:lineno:reason', got {raw!r}")
        relpath, lineno, reason = parts[0].strip(), parts[1].strip(), parts[2].strip()
        try:
            position = int(lineno)
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: lineno must be an int, got {lineno!r}") from exc
        key = (relpath, position)
        if key in entries:
            raise ValueError(f"{path}:{number}: duplicate entry for {relpath}:{position}")
        entries[key] = reason
    return entries


def format_findings(findings: Iterable[Finding]) -> str:
    """One line per finding, for an assertion message."""

    return "\n".join(str(finding) for finding in findings)


def match_allowlist(
    findings: Sequence[Finding], allowed: Mapping[tuple[str, int], str]
) -> tuple[list[Finding], list[tuple[str, int]]]:
    """Split ``findings`` into (unallowlisted, stale allowlist keys).

    Matching is exact ``(path, lineno)`` first. A repository under concurrent
    edits moves lines under an allowlist entry for reasons that have nothing to
    do with the silence, so a leftover finding and a leftover entry **on the
    same path** are re-paired when there is exactly one of each; more than one
    on a path means the repair cannot tell which is which and is skipped (the
    entry is reported stale rather than silently re-labelled).

    The caller must keep the count pinned: a repair only ever absorbs a *moved*
    silence, never an additional one.
    """

    free: set[tuple[str, int]] = set(allowed)
    unmatched: list[Finding] = []
    for finding in findings:
        key = (finding.path, finding.lineno)
        if key in free:
            free.discard(key)
        else:
            unmatched.append(finding)

    by_path: dict[str, list[Finding]] = {}
    for finding in unmatched:
        by_path.setdefault(finding.path, []).append(finding)

    still_unmatched: list[Finding] = []
    for path, candidates in by_path.items():
        entries = sorted(key for key in free if key[0] == path)
        if len(candidates) == 1 and len(entries) == 1:
            free.discard(entries[0])
            continue
        still_unmatched.extend(candidates)

    still_unmatched.sort(key=lambda finding: (finding.path, finding.lineno))
    return still_unmatched, sorted(free)
