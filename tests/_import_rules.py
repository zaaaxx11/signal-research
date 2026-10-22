"""AST enforcement of the dependency rules in 02 §3.1 / 05 §3.4.

Architecture rules that are only comments rot. This module parses every file
under ``app/`` and returns violations; ``tests/test_import_direction.py`` runs
it on the real tree and on synthetic bad sources (so the checker itself is
proven to bite).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

HTTP_CLIENTS = frozenset({"httpx", "httpx2", "requests", "aiohttp", "urllib3"})
LEGACY_TRANSPORT = frozenset({"urllib", "requests"})
DATAFRAMES = frozenset({"pandas", "numpy", "polars", "fastparquet"})
IO_LIBS = frozenset({"sqlalchemy", "aiosqlite", "sqlite3", "socket"}) | HTTP_CLIENTS

VERIFY_ALLOWED_APP = frozenset({"app.verify", "app.verify.units", "app.verify.rules"})


@dataclass(frozen=True)
class Violation:
    rule: str
    module: str
    target: str
    lineno: int

    def __str__(self) -> str:
        return f"[{self.rule}] {self.module}:{self.lineno} imports {self.target}"


def _root(target: str) -> str:
    return target.split(".", 1)[0]


def _resolve_relative(module: str, is_package: bool, level: int, target: str | None) -> str:
    package = module if is_package else module.rsplit(".", 1)[0]
    parts = package.split(".")
    up = level - 1
    if up:
        parts = parts[:-up] if up <= len(parts) else []
    base = ".".join(parts)
    if target:
        return f"{base}.{target}" if base else target
    return base


def _rules(module: str, target: str, node: ast.AST) -> list[Violation]:
    found: list[Violation] = []
    lineno = getattr(node, "lineno", 0)
    root = _root(target)

    def add(rule: str) -> None:
        found.append(Violation(rule=rule, module=module, target=target, lineno=lineno))

    # 1. contracts.py is the root of the vocabulary: no app.* imports at all.
    if module == "app.contracts" and (target == "app" or target.startswith("app.")):
        add("contracts-isolation")

    # 2. One HTTP seam. Everything else must go through app.providers.http.
    if root in {"httpx", "httpx2"} and module != "app.providers.http":
        add("http-seam")

    # 3. No legacy transports anywhere (05 Gate 1 grep).
    if root in LEGACY_TRANSPORT:
        add("no-legacy-transport")

    # 4. No dataframe stacks in production code (05 §3.4 rule 3).
    if root in DATAFRAMES:
        add("no-dataframes")

    # 5. providers/ may not know about enrichment or export.
    if module.startswith("app.providers") and (
        target == "app.enrich"
        or target.startswith("app.enrich.")
        or target == "app.export"
        or target.startswith("app.export.")
    ):
        add("providers-isolation")

    # 6. verify/ is pure: only its own modules, no DB/HTTP/socket.
    if module == "app.verify" or module.startswith("app.verify."):
        if target.startswith("app.") and target not in VERIFY_ALLOWED_APP:
            add("verify-pure")
        if root in IO_LIBS or root == "socket":
            add("verify-pure")

    # 7. export/ receives rows + columns, never a provider.
    if module.startswith("app.export") and (
        target == "app.providers" or target.startswith("app.providers.")
    ):
        add("export-no-providers")

    # 8. api/ may see the registry, never an adapter.
    if module.startswith("app.api") and target.startswith("app.providers.adapters"):
        add("api-no-adapters")

    # 9. No raw sockets in application code (the HTTP client owns them).
    if root == "socket":
        add("no-raw-socket")

    return found


def _dynamic_imports(module: str, tree: ast.AST) -> list[Violation]:
    found: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in {"__import__", "import_module"}:
            found.append(
                Violation(
                    rule="no-dynamic-import",
                    module=module,
                    target=ast.unparse(func),
                    lineno=getattr(node, "lineno", 0),
                )
            )
    return found


def check_source(source: str, module: str, *, filename: str = "<memory>") -> list[Violation]:
    """Return every violation in one module source."""

    tree = ast.parse(source, filename=filename)
    is_package = module.endswith(".__init__")
    normalized = module[: -len(".__init__")] if is_package else module
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                violations.extend(_rules(normalized, alias.name, node))
        elif isinstance(node, ast.ImportFrom):
            target = node.module or ""
            if node.level:
                target = _resolve_relative(normalized, is_package, node.level, node.module)
            if target:
                violations.extend(_rules(normalized, target, node))
    violations.extend(_dynamic_imports(normalized, tree))
    return violations


def _module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["app", *parts]) if root.name == "app" else ".".join(parts)


def check_tree(root: Path | None = None) -> list[Violation]:
    """Parse every module under ``app/`` and return all violations."""

    app_root = root or APP_ROOT
    violations: list[Violation] = []
    for path in sorted(app_root.rglob("*.py")):
        module = _module_name(path, app_root)
        source = path.read_text(encoding="utf-8")
        violations.extend(check_source(source, module, filename=str(path)))
    return violations
