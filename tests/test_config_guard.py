"""A typo'd ``MEE_*`` variable must not be ignored in silence.

On 2026-10-29 ``MEE_DEPTH_UNIVERSE`` was written into ``engine.env`` while the
setting is ``MEE_DEPTH_UNIVERSE_PATH``. pydantic-settings looks up the names it
models and ignores every other key by design, so the collector fell back to its
default universe and the depth store served 10,429 symbols with 286 rich rows
instead of 4,217 correct ones. Nothing warned; the operator found out from the
data.

These tests pin the guard that closes that class of bug:

* the pure function reports a ``MEE_*`` key the application does not read and
  stays quiet for every name it does read (settings fields, their aliases, and
  the names code reads straight out of ``os.environ``);
* a suggestion is offered only when it is close enough to be worth typing —
  a nonsense name gets none, because a wild guess is worse than silence;
* startup logs exactly one WARNING per offending key and never raises unless
  ``MEE_STRICT_ENV=1``;
* the reconciliation test below scans ``app/`` for direct environment reads, so
  the allowlist cannot rot: adding ``os.environ.get("MEE_NEW")`` without
  modelling it fails here, not in production.
"""

from __future__ import annotations

import ast
import logging
import os
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from app import config_guard
from app.config import Settings
from app.main import create_app

#: The four names the deployment's ``engine.env`` carries that no Settings field
#: produces. They are the reason the allowlist file exists at all.
DEPLOYMENT_DIRECT_READS = (
    "MEE_ENGINE_SNAPSHOT",
    "MEE_ENGINE_CACHE_DIR",
    "MEE_SEC_RATE_LIMIT",
    "MEE_DATA_DIR",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """An environment with no ``MEE_*`` key, so a test owns every finding."""

    for name in [key for key in os.environ if key.upper().startswith("MEE_")]:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def mee_records() -> Iterator[list[logging.LogRecord]]:
    """Capture what the ``mee`` tree emits, propagate flag or not.

    ``_configure_logging`` sets ``propagate = False`` on ``mee``, so ``caplog``
    (a root-logger handler) sees nothing. A handler on the tree itself is what
    an operator's journal sees, so that is what these tests read.
    """

    logger = logging.getLogger("mee")
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    saved_level, saved_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate


def _warnings(records: list[logging.LogRecord]) -> list[str]:
    return [
        record.getMessage()
        for record in records
        if record.levelno == logging.WARNING and "unknown MEE_*" in record.getMessage()
    ]


# --------------------------------------------------------------------------
# the pure function
# --------------------------------------------------------------------------


def test_a_typo_of_a_real_setting_is_reported() -> None:
    """The incident, verbatim: one character too few, and nothing noticed."""

    assert config_guard.unknown_env_keys({"MEE_DEPTH_UNIVERSE": "u.txt"}) == [
        "MEE_DEPTH_UNIVERSE"
    ]


def test_the_correct_name_is_not_reported() -> None:
    assert config_guard.unknown_env_keys({"MEE_DEPTH_UNIVERSE_PATH": "u.txt"}) == []


@pytest.mark.parametrize("name", DEPLOYMENT_DIRECT_READS)
def test_the_names_code_reads_directly_are_not_reported(name: str) -> None:
    assert config_guard.unknown_env_keys({name: "x"}) == []


def test_the_strict_switch_is_itself_known() -> None:
    """Opting into strict mode must not be the first thing strict mode rejects."""

    assert config_guard.STRICT_ENV in config_guard.known_env_names()
    assert config_guard.unknown_env_keys({config_guard.STRICT_ENV: "1"}) == []


def test_variables_outside_the_prefix_are_ignored() -> None:
    """``PATH`` and friends are not this application's business."""

    environ = {"PATH": "/usr/bin", "HOME": "/home/ubuntu", "POLYGON_API_KEY": "k"}
    assert config_guard.unknown_env_keys(environ) == []


def test_every_offender_is_reported_not_just_the_first() -> None:
    environ = {
        "MEE_DEPTH_UNIVERSE": "u.txt",
        "MEE_DB_PATH": "ztockz.db",
        "MEE_QUOTE_UNIVERSE": '["AAPL"]',
    }
    assert config_guard.unknown_env_keys(environ) == [
        "MEE_DEPTH_UNIVERSE",
        "MEE_QUOTE_UNIVERSE",
    ]


def test_an_explicit_known_set_is_authoritative() -> None:
    """The function is pure: a caller's ``known`` wins over the ambient set."""

    assert config_guard.unknown_env_keys(
        {"MEE_ZZZZ": "1", "MEE_DB_PATH": "db"}, known={"MEE_DB_PATH"}
    ) == ["MEE_ZZZZ"]


# --------------------------------------------------------------------------
# suggestions
# --------------------------------------------------------------------------


def test_the_suggestion_is_the_real_setting_name() -> None:
    assert (
        config_guard.suggest("MEE_DEPTH_UNIVERSE", config_guard.known_env_names())
        == "MEE_DEPTH_UNIVERSE_PATH"
    )


@pytest.mark.parametrize(
    ("typo", "expected"),
    [
        ("MEE_DB_PATHS", "MEE_DB_PATH"),
        ("MEE_PROVIDER_MOD", "MEE_PROVIDER_MODE"),
        ("MEE_SEC_RATE_LIMITS", "MEE_SEC_RATE_LIMIT"),
        ("MEE_ENGINE_SNAPSHOT_PATH", "MEE_ENGINE_SNAPSHOT"),
    ],
)
def test_short_typos_resolve_to_the_intended_name(typo: str, expected: str) -> None:
    assert config_guard.suggest(typo, config_guard.known_env_names()) == expected


@pytest.mark.parametrize("nonsense", ["MEE_ZZZZ", "MEE_FOO", "MEE_TEST", "MEE_ABCDEFGH"])
def test_a_nonsense_name_gets_no_guess(nonsense: str) -> None:
    """A wrong suggestion sends the operator to the wrong variable with a smile."""

    assert config_guard.suggest(nonsense, config_guard.known_env_names()) is None


def test_a_sibling_that_fits_equally_well_gets_no_guess() -> None:
    """``MEE_DEPTH_SEC`` fits ``_BULK`` and ``_SNAPSHOT`` equally: silence beats a coin flip."""

    known = config_guard.known_env_names()
    assert config_guard.suggest("MEE_DEPTH_SEC", known) is None


def test_a_lowercase_typo_still_finds_the_canonical_name() -> None:
    assert (
        config_guard.suggest("mee_depth_universe", config_guard.known_env_names())
        == "MEE_DEPTH_UNIVERSE_PATH"
    )


def test_suggest_survives_an_empty_known_set() -> None:
    assert config_guard.suggest("MEE_DEPTH_UNIVERSE", set()) is None


# --------------------------------------------------------------------------
# the known set follows Settings, and the allowlist stays small
# --------------------------------------------------------------------------


def test_every_settings_field_contributes_its_environment_name() -> None:
    """Hard-coded lists rot; ``Settings.model_fields`` is the source of truth."""

    expected = {f"MEE_{name.upper()}" for name in Settings.model_fields}
    assert config_guard.field_env_names() == expected
    assert expected <= config_guard.known_env_names()


def test_the_guard_tracks_the_settings_prefix() -> None:
    """If the prefix ever changes, the guard must be told — not silently wrong."""

    assert Settings.model_config.get("env_prefix") == config_guard.ENV_PREFIX


def test_the_alternate_spellings_settings_accepts_are_known() -> None:
    """``MEE_SCREEN_DEPTH_STORE`` is a documented alias, not a typo."""

    assert "MEE_SCREEN_DEPTH_STORE" in config_guard.settings_env_names()
    assert config_guard.unknown_env_keys({"MEE_SCREEN_DEPTH_STORE": "d.json"}) == []


def test_a_lowercase_spelling_is_not_reported(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """pydantic-settings folds case, so ``mee_db_path`` really does set ``db_path``.

    Flagging it would be the false alarm that teaches an operator to ignore the
    guard. This test pins the assumption: if a future pydantic-settings starts
    matching exactly, it fails here and the guard gains a branch.
    """

    database = tmp_path / "lowercase.db"
    clean_env.setenv("mee_db_path", str(database))

    assert Settings().db_path == database
    assert config_guard.unknown_env_keys({"mee_db_path": str(database)}) == []


def test_a_name_padded_with_whitespace_is_reported() -> None:
    """``env 'MEE_DB_PATH =x'`` creates a key nothing reads — say so."""

    assert config_guard.unknown_env_keys({"MEE_DB_PATH ": "x"}) == ["MEE_DB_PATH "]
    assert config_guard.suggest("MEE_DB_PATH ", config_guard.known_env_names()) == "MEE_DB_PATH"


@pytest.mark.parametrize("value", ["", "0", "maybe", "off", "false"])
def test_strict_mode_needs_an_explicit_true(
    clean_env: pytest.MonkeyPatch, value: str
) -> None:
    clean_env.setenv(config_guard.STRICT_ENV, value)
    clean_env.setenv("MEE_DEPTH_UNIVERSE", "universe.txt")

    create_app()  # warns, does not refuse


def test_the_allowlist_carries_the_deployment_direct_reads() -> None:
    names = config_guard.allowlist_names()
    assert set(DEPLOYMENT_DIRECT_READS) <= names


def test_the_allowlist_has_no_entry_that_is_a_settings_field() -> None:
    """A stale entry hides a real typo: it would make a dead name look read."""

    fields = {f"MEE_{name.upper()}" for name in Settings.model_fields}
    assert config_guard.allowlist_names() & fields == set()


def test_every_allowlist_entry_explains_itself() -> None:
    """A name without a reader is an assumption; the reason makes it checkable."""

    lines = [
        line.strip()
        for line in config_guard.ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert lines
    for line in lines:
        name, separator, reason = line.partition(":")
        assert separator == ":", f"no reason after ': ' on {line!r}"
        assert reason.strip(), f"empty reason for {name!r}"


# --------------------------------------------------------------------------
# reconcile the allowlist with what the code actually reads
# --------------------------------------------------------------------------


def _direct_env_reads() -> set[str]:
    """Every ``MEE_*`` name ``app/`` hands to an environment lookup."""

    app_dir = Path(config_guard.__file__).resolve().parent
    found: set[str] = set()
    for path in sorted(app_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = _callee(node.func).lower()
            if "env" not in callee:
                continue
            for argument in node.args[:2]:
                value = _name_of(argument, constants)
                if value and value.startswith("MEE_"):
                    found.add(value)
    return found


def _module_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant):
            if isinstance(value.value, str):
                constants[target.id] = value.value
    return constants


def _callee(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_callee(node.value)}.{node.attr}"
    return ""


def _name_of(node: ast.expr, constants: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def test_every_direct_environment_read_is_in_the_known_set() -> None:
    """The guard must know every name the code reads, or it cries wolf.

    This is the test that keeps the allowlist honest: a new
    ``os.environ.get("MEE_NEW")`` that is neither a Settings field nor
    allowlisted fails here, with the added name in the message.
    """

    found = _direct_env_reads()
    assert len(found) >= 10, "the scan stopped finding reads; the AST walk is broken"
    assert found - config_guard.known_env_names() == set()


# --------------------------------------------------------------------------
# startup
# --------------------------------------------------------------------------


def test_startup_logs_one_warning_for_the_typo(
    clean_env: pytest.MonkeyPatch, mee_records: list[logging.LogRecord]
) -> None:
    clean_env.setenv("MEE_DEPTH_UNIVERSE", "universe.txt")

    create_app()  # must not raise: a typo is a report, not an outage

    assert _warnings(mee_records) == [
        "unknown MEE_* setting in the environment: MEE_DEPTH_UNIVERSE "
        "(did you mean MEE_DEPTH_UNIVERSE_PATH?)"
    ]


def test_startup_logs_one_warning_per_offending_key(
    clean_env: pytest.MonkeyPatch, mee_records: list[logging.LogRecord]
) -> None:
    clean_env.setenv("MEE_DEPTH_UNIVERSE", "universe.txt")
    clean_env.setenv("MEE_ZZZZ", "1")

    create_app()

    assert _warnings(mee_records) == [
        "unknown MEE_* setting in the environment: MEE_DEPTH_UNIVERSE "
        "(did you mean MEE_DEPTH_UNIVERSE_PATH?)",
        "unknown MEE_* setting in the environment: MEE_ZZZZ",
    ]


def test_startup_is_silent_when_the_environment_is_clean(
    clean_env: pytest.MonkeyPatch, mee_records: list[logging.LogRecord]
) -> None:
    for name in (*DEPLOYMENT_DIRECT_READS, "MEE_DEPTH_UNIVERSE_PATH", "MEE_DB_PATH"):
        clean_env.setenv(name, "x")
    clean_env.setenv(config_guard.STRICT_ENV, "0")

    create_app()

    assert _warnings(mee_records) == []
    assert [r for r in mee_records if r.levelno >= logging.WARNING] == []


def test_strict_mode_refuses_to_start_and_names_every_offender(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv(config_guard.STRICT_ENV, "1")
    clean_env.setenv("MEE_DEPTH_UNIVERSE", "universe.txt")
    clean_env.setenv("MEE_ZZZZ", "1")

    with pytest.raises(config_guard.UnknownEnvSettingError) as caught:
        create_app()

    message = str(caught.value)
    assert "MEE_DEPTH_UNIVERSE (did you mean MEE_DEPTH_UNIVERSE_PATH?)" in message
    assert "MEE_ZZZZ" in message


def test_strict_mode_starts_a_clean_environment(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv(config_guard.STRICT_ENV, "1")
    clean_env.setenv("MEE_DB_PATH", "ztockz.db")

    create_app()


# --------------------------------------------------------------------------
# the guard itself must never be the reason a process cannot start
# --------------------------------------------------------------------------


def test_a_missing_allowlist_is_not_a_crash(tmp_path: Path) -> None:
    missing = tmp_path / "nope.txt"
    assert config_guard.allowlist_names(missing) == frozenset()
    assert "MEE_DEPTH_UNIVERSE_PATH" in config_guard.known_env_names(
        allowlist_path=missing
    )


def test_malformed_allowlist_lines_are_skipped(tmp_path: Path) -> None:
    allowlist = tmp_path / "allowlist.txt"
    allowlist.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "MEE_DATA_DIR: read by app/engine/paths.py",
                "MEE_DB_PATH: no reason needed here, it is a field",  # stale, but parsed
                "not a name: whatever",
                ": no name at all",
                "MEE_lower_case: not a name",
                "MEE-1: not a name",
            ]
        ),
        encoding="utf-8",
    )

    assert config_guard.allowlist_names(allowlist) == {
        "MEE_DATA_DIR",
        "MEE_DB_PATH",
    }


def test_an_allowlist_that_cannot_be_read_is_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable file is a deployment problem, not a reason to refuse boot."""

    allowlist = tmp_path / "allowlist.txt"
    allowlist.write_text("MEE_DATA_DIR: read by app/engine/paths.py\n", encoding="utf-8")
    real_read_text = Path.read_text

    def _denied(self: Path, *args: object, **kwargs: object) -> str:
        if self == allowlist:
            raise PermissionError("denied")
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", _denied)
    assert config_guard.allowlist_names(allowlist) == frozenset()


def test_non_string_keys_and_values_do_not_crash() -> None:
    """``os.environ`` is always strings; a caller's Mapping need not be.

    The value is never inspected — only the key decides whether the application
    reads the setting — so a non-string value cannot turn a known name into a
    finding, and a non-string key is simply not a variable name.
    """

    environ = {1: "MEE_ZZZZ", None: "x", "MEE_DB_PATH": 3, "MEE_ZZZZ": object()}
    reported = config_guard.unknown_env_keys(environ)  # type: ignore[arg-type]
    assert "MEE_DB_PATH" not in reported
    assert reported == ["MEE_ZZZZ"]


def test_the_guard_reads_the_ambient_environment_at_call_time(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("MEE_DEPTH_UNIVERSE", "universe.txt")
    assert config_guard.check_startup() == ["MEE_DEPTH_UNIVERSE"]
