"""AST dependency-rule enforcement (02 §3.1; 05 §3.4 rules 2-4).

The real tree must be clean. The checker is also run against synthetic bad
sources, so a checker that silently stops matching cannot pass this test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._import_rules import check_source, check_tree

ROOT = Path(__file__).resolve().parents[1]


def test_app_tree_has_no_import_violations() -> None:
    files = sorted((ROOT / "app").rglob("*.py"))
    assert len(files) >= 20, f"expected the phase-0 module tree, found {len(files)} files"

    violations = check_tree(ROOT / "app")
    assert violations == [], "\n".join(str(violation) for violation in violations)


def test_http_seam_allows_httpx2_only_in_providers_http() -> None:
    assert check_source("import httpx2\n", "app.providers.http") == []
    assert check_source("from httpx2 import AsyncClient\n", "app.providers.http") == []


@pytest.mark.parametrize(
    ("source", "module", "expected_rule"),
    [
        ("from app.config import Settings\n", "app.contracts", "contracts-isolation"),
        ("from app.providers.adapters import fake\n", "app.api.markets", "api-no-adapters"),
        ("import httpx2\n", "app.providers.adapters.fake", "http-seam"),
        ("import sqlalchemy\n", "app.verify.rules", "verify-pure"),
        ("from app.providers.registry import REGISTRY\n", "app.export.writer", "export-no-providers"),
        ("import pandas as pd\n", "app.export.writer", "no-dataframes"),
        ("import requests\n", "app.providers.http", "no-legacy-transport"),
        ("import importlib\nimportlib.import_module('x')\n", "app.providers.registry", "no-dynamic-import"),
        ("import socket\n", "app.jobs.repository", "no-raw-socket"),
    ],
)
def test_checker_detects_violations(source: str, module: str, expected_rule: str) -> None:
    rules = {violation.rule for violation in check_source(source, module)}
    assert expected_rule in rules, f"expected {expected_rule}, got {sorted(rules)}"
