"""Refuse to let a typo'd ``MEE_*`` variable pass in silence.

On 2026-10-29 ``MEE_DEPTH_UNIVERSE`` was written into ``engine.env`` while the
setting is ``MEE_DEPTH_UNIVERSE_PATH``. pydantic-settings resolves the names it
models and ignores every other key, by design and without a word, so the
collector fell back to its default universe and the depth store served 10,429
symbols with 286 rich rows instead of 4,217 correct ones. The process started
clean, the log said nothing, and the damage surfaced in the data.

This module names that failure at startup. It is deliberately a **report** and
not a check: an unknown variable is a warning, because refusing to boot over a
stray key in a shell turns a cosmetic mistake into an outage. ``MEE_STRICT_ENV=1``
is the deployment's opt-in to the other behaviour — refuse to start until the
environment is clean.

The known set is derived, never typed out by hand, so it cannot rot:

* every name a ``Settings`` field produces (``MEE_`` + the field name);
* every alternate spelling a field declares through ``validation_alias``
  (``MEE_SCREEN_DEPTH_STORE`` and ``MEE_ENGINE_SNAPSHOT`` are real variables);
* every name in ``config_guard_allowlist.txt``, for the variables code reads
  straight out of ``os.environ`` — Settings does not model those, so nothing
  would otherwise know they are legitimate;
* ``MEE_STRICT_ENV`` itself, which this module reads.

Precision is the whole point. A guard that flags a legitimate key teaches the
operator to ignore it, and then the real typo is ignored too, so membership is
checked case-insensitively (pydantic-settings matches env names that way: a
lowercase ``mee_db_path`` *is* read) and a suggestion is only offered when the
name is genuinely close to a real one — a wild guess would send the operator to
the wrong variable with a smile, and so would a sibling picked by a coin flip.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
from collections.abc import Collection, Mapping
from pathlib import Path

from pydantic import AliasChoices

from app.config import Settings

logger = logging.getLogger("mee.config")

ENV_PREFIX = "MEE_"
#: Set to a true spelling to make an unknown key fatal at startup.
STRICT_ENV = "MEE_STRICT_ENV"
#: Next to this module so a wheel, an sdist and a checkout all find it.
ALLOWLIST_PATH = Path(__file__).with_name("config_guard_allowlist.txt")
#: High enough that ``MEE_ZZZZ`` gets no guess (its best match scores 0.47) and
#: low enough that ``MEE_DEPTH_UNIVERSE`` still reaches the real name (0.88).
SUGGESTION_CUTOFF = 0.8
#: Two candidates within this much of each other are a coin flip, and a coin
#: flip is a misleading hint: ``MEE_DEPTH_SEC`` is equally close to
#: ``MEE_DEPTH_SEC_BULK`` and ``MEE_DEPTH_SEC_SNAPSHOT``, so the honest answer
#: there is no suggestion at all. Small enough that a typo still wins its own
#: name: every one-character deletion of every known name resolves correctly,
#: and a sampled two-character damage test misses only 3 of 480.
AMBIGUITY_MARGIN = 0.02

_NAME = re.compile(rf"^{ENV_PREFIX}[A-Z0-9_]+$")
_TRUE = frozenset({"1", "true", "yes", "on"})


class UnknownEnvSettingError(RuntimeError):
    """Strict mode: the environment carries a ``MEE_*`` name nothing reads."""


def allowlist_names(path: Path | None = None) -> frozenset[str]:
    """The names the allowlist file admits, or an empty set if it cannot be read.

    The parser is tolerant on purpose: a guard must never be the reason a
    process cannot start. A missing file, an unreadable one and an undecodable
    one all mean "no exceptions today" — the same view the code had before this
    module existed. A malformed line (no name, a name that is not
    ``MEE_[A-Z0-9_]+``) is skipped rather than admitted, because a junk entry
    would suppress a genuine finding.
    """

    target = ALLOWLIST_PATH if path is None else path
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return frozenset()

    names: set[str] = set()
    for line in text.splitlines():
        body = line.strip()
        if not body or body.startswith("#"):
            continue
        # "NAME: reason" — the reason is documentation, the name is the datum.
        name = body.partition(":")[0].strip()
        if _NAME.fullmatch(name):
            names.add(name)
    return frozenset(names)


def field_env_names() -> frozenset[str]:
    """Every environment name a ``Settings`` field produces.

    Read from ``Settings.model_fields`` on each call rather than snapshotted at
    import, so a field added later is known without touching this module.
    """

    return frozenset(f"{ENV_PREFIX}{name.upper()}" for name in Settings.model_fields)


def alias_env_names() -> frozenset[str]:
    """Every alternate ``MEE_*`` spelling a field declares.

    A field may accept a documented name that its own name does not produce
    (``screen_depth_store_path`` accepts ``MEE_SCREEN_DEPTH_STORE``). Those are
    really read, so flagging them would be the exact false positive this module
    must not produce.
    """

    names: set[str] = set()
    for field in Settings.model_fields.values():
        alias = field.validation_alias
        choices: tuple[object, ...] = (
            tuple(alias.choices) if isinstance(alias, AliasChoices) else (alias,)
        )
        for choice in choices:
            if isinstance(choice, str) and choice.upper().startswith(ENV_PREFIX):
                names.add(choice)
    return frozenset(names)


def settings_env_names() -> frozenset[str]:
    """Everything ``Settings`` itself reads from the environment."""

    return field_env_names() | alias_env_names()


def known_env_names(*, allowlist_path: Path | None = None) -> frozenset[str]:
    """Every ``MEE_*`` name this application reads, however it reads it."""

    return (
        settings_env_names()
        | allowlist_names(allowlist_path)
        | {STRICT_ENV}
    )


def unknown_env_keys(
    environ: Mapping[str, str], *, known: Collection[str] | None = None
) -> list[str]:
    """The ``MEE_*`` keys in ``environ`` that this application never reads.

    Sorted so a report is stable between runs and two offenders never trade
    places in a diff. Values are not inspected — only the key decides whether a
    setting is read — so a caller's non-string value cannot invent a finding,
    and a non-string key is not a variable name at all.

    Membership folds case because pydantic-settings does (``case_sensitive`` is
    ``False``): ``mee_db_path`` really does set ``db_path``, so calling it
    unknown would be a false alarm.
    """

    admitted = known_env_names() if known is None else known
    folded = {name.upper() for name in admitted if isinstance(name, str)}

    found: list[str] = []
    for key in environ:
        if not isinstance(key, str):
            continue
        if not key.upper().startswith(ENV_PREFIX):
            continue
        if key.upper() in folded:
            continue
        found.append(key)
    return sorted(found)


def suggest(name: str, known: Collection[str] | None = None) -> str | None:
    """The real setting ``name`` most likely meant, or ``None`` when unsure.

    ``difflib`` on the folded name: the operator's typo is usually a missing
    suffix (``MEE_DEPTH_UNIVERSE``), a plural, or a lowercase copy, and all
    three are near-misses on the canonical spelling. Below
    :data:`SUGGESTION_CUTOFF` the honest answer is silence, and so it is when
    two names are equally close — a sibling picked by a coin flip sends the
    operator to the wrong variable with a smile.
    """

    if not isinstance(name, str):
        return None
    admitted = known_env_names() if known is None else known
    # Folding maps a candidate back to its canonical spelling, so a suggestion
    # is always a name the operator can paste into the environment file.
    canonical = {
        candidate.upper(): candidate
        for candidate in admitted
        if isinstance(candidate, str)
    }
    if not canonical:
        return None

    folded = name.upper()
    matches = difflib.get_close_matches(
        folded, list(canonical), n=2, cutoff=SUGGESTION_CUTOFF
    )
    if not matches:
        return None
    if len(matches) > 1:
        best = difflib.SequenceMatcher(None, folded, matches[0]).ratio()
        runner_up = difflib.SequenceMatcher(None, folded, matches[1]).ratio()
        if best - runner_up < AMBIGUITY_MARGIN:
            return None
    return canonical[matches[0]]


def _strict(environ: Mapping[str, str]) -> bool:
    """Whether this environment opted into refusing to start."""

    raw = environ.get(STRICT_ENV, "")
    return isinstance(raw, str) and raw.strip().lower() in _TRUE


def check_startup(
    environ: Mapping[str, str] | None = None,
    *,
    log: logging.Logger | None = None,
    known: Collection[str] | None = None,
) -> list[str]:
    """Report unknown ``MEE_*`` keys once per key; raise in strict mode.

    Called from ``create_app`` before the runtime exists, so a mistyped
    variable is named in the journal at the moment the process comes up rather
    than inferred from the data a day later. Returns the offenders so a caller
    (or a test) does not have to parse the log to learn them.
    """

    env = os.environ if environ is None else environ
    admitted = known_env_names() if known is None else known
    unknown = unknown_env_keys(env, known=admitted)
    if not unknown:
        return []

    hints = [(name, suggest(name, admitted)) for name in unknown]

    if _strict(env):
        listed = ", ".join(
            f"{name} (did you mean {hint}?)" if hint else name for name, hint in hints
        )
        raise UnknownEnvSettingError(
            f"refusing to start with unknown MEE_* environment setting(s): {listed}. "
            "Fix the spelling, remove the variable, or add the name to "
            f"{ALLOWLIST_PATH.name} if the application reads it directly. "
            f"Unset {STRICT_ENV} to start anyway."
        )

    sink = log if log is not None else logger
    for name, hint in hints:
        if hint:
            sink.warning(
                "unknown MEE_* setting in the environment: %s (did you mean %s?)",
                name,
                hint,
            )
        else:
            sink.warning("unknown MEE_* setting in the environment: %s", name)
    return unknown


__all__ = [
    "ALLOWLIST_PATH",
    "AMBIGUITY_MARGIN",
    "ENV_PREFIX",
    "STRICT_ENV",
    "SUGGESTION_CUTOFF",
    "UnknownEnvSettingError",
    "alias_env_names",
    "allowlist_names",
    "check_startup",
    "field_env_names",
    "known_env_names",
    "settings_env_names",
    "suggest",
    "unknown_env_keys",
]
