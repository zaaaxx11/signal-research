"""Finviz option tokens are filters: one token, one predicate over one column.

Three catalogue keys do not name a single row column. ``ta_perf`` and its twin
``ta_perf2`` offer ten performance windows, and ``ta_volatility`` two, so
``FilterSpec.field`` is ``None`` for them (``FilterSpec.choice`` is ``True``).
This module is the one place that turns the token a caller sends into the
predicate the row layer already knows how to run.

The request shape does not change: a criterion is still ``{key, op, value}``,
with ``op`` ``eq`` and ``value`` the Finviz option token, e.g.
``{"key": "ta_perf", "op": "eq", "value": "1w10u"}``. What ``eq`` means here is
"this bucket": the token names a window *and* a direction, and the result is an
ordinary ``field op number`` :class:`app.screen.filters.Filter`. A null cell
therefore still matches nothing — an unknown return is not a return of zero.

Performance tokens — ``<window><direction>``
--------------------------------------------

The window prefix picks the row column:

* ``d`` → ``change_pct`` — the current session;
* ``1w`` → ``ret_1w``, ``4w`` → ``ret_1m``, ``13w`` → ``ret_3m``,
  ``26w`` → ``ret_6m``, ``ytd`` → ``ret_ytd``, ``52w`` → ``ret_1y``;
* ``5y`` → ``ret_5y`` and ``10y`` → ``ret_10y``;
* ``3y`` → **no column**: refused by name, see below.

``4w``/``13w``/``26w``/``52w`` are Finviz's week spellings of the groups its own
dropdown labels Month, Quarter, Half Year and Year, and ``ret_1m``/``ret_3m``/
``ret_6m``/``ret_1y`` are the provider's columns for exactly those windows. The
day window is the current session's change, which the light row has always
carried as ``change_pct``.

Four directions per window:

* ``up``   → ``> 0``    — the window's return is positive;
* ``down`` → ``< 0``    — negative (exactly 0 is neither up nor down);
* ``<N>u`` → ``<= -N``  — down *at least* N percent; ``u`` is Finviz's "under";
* ``<N>o`` → ``>= N``   — up at least N percent; ``o`` is "over".

The sign is the part worth stating: ``1w10u`` is *week down 10%*, not "week
under +10%". The probe's own token order is the proof — ``1w30u, 1w20u, 1w10u,
1wdown, 1wup, 1w10o, 1w20o, 1w30o`` is monotone only when ``u`` means the
negative side. Boundaries are inclusive, so a row at exactly ``-10`` sits in the
"down 10%" bucket instead of in neither.

Volatility tokens — ``<wo|mo><N>``
----------------------------------

``woN`` → ``volatility_w >= N`` and ``moN`` → ``volatility_m >= N``: Finviz's
"Volatility (Week): Over 2%" asks for a weekly volatility of at least two
percent, and the row's ``volatility_w``/``volatility_m`` are that percentage.

What is not invented
--------------------

* **The vocabulary is the catalogue's.** A token is accepted only when
  ``FILTER_CATALOG[key].options`` carries it. ``v2``, ``1w15u`` or ``wo11`` is
  an :class:`UnknownChoiceOption` (422) — never rounded to the nearest bucket.
* **The window's column is the row's column.** Every prefix names a field in
  :data:`app.screen.filters.FIELDS` that a parity row really carries, and the
  boot gate below checks it against :data:`app.screen.universe.CARRIED_FIELDS`.
  The module never computes a return of its own.
* **One window has no column, and says so.** No screen row carries a three-year
  return: the parity scan requests ``Perf.W``/``1M``/``3M``/``6M``/``YTD``/
  ``Y``/``5Y``/``10Y`` (``ret_1w`` … ``ret_10y``), while ``ret_3y`` exists only
  on the deep/quote path. Every ``3y…`` token is therefore an
  :class:`UnsupportedChoiceOption` that names the missing column and the nine
  windows that do compile. Approximating ``3ydown`` from ``ret_1y``/``ret_5y``
  would be a fabricated bucket — the expensive kind of wrong for a screen.
* **No control token is a filter.** ``""`` (the panel's empty value), ``modal``
  and ``modal_intraday`` are UI controls the probe recorded, not buckets.

Every accepted token compiles through
:func:`app.screen.filters.compile_filter`, so what reaches
:func:`app.screen.filters.apply_filters` is a normal numeric predicate over a
real column — this module adds no operator, no field and no arithmetic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.screen.filters import FIELDS, FILTER_CATALOG, Filter, compile_filter

#: Tokens the probe recorded for a control rather than for a bucket: the empty
#: value, the "more…" modal and the intraday modal. A request that sends one is
#: refused like any other token that is not part of the filter vocabulary.
CONTROL_TOKENS: frozenset[str] = frozenset({"", "modal", "modal_intraday"})

#: ``5u`` / ``10o`` — a threshold and the side it applies to.
_THRESHOLD = re.compile(r"^(?P<number>\d+(?:\.\d+)?)(?P<side>[uo])$")

#: ``2`` / ``15`` — the volatility threshold in percent (Finviz's ``wo2``…).
_COUNT = re.compile(r"^(?P<number>\d+(?:\.\d+)?)$")


@dataclass(frozen=True, slots=True)
class Window:
    """One Finviz window and the row column that answers it.

    ``field`` is ``None`` for the window no row carries; ``missing`` then says
    why, in the sentence a 422 repeats. A window never carries both.
    """

    token: str
    label: str
    field: str | None = None
    missing: str | None = None

    @property
    def is_carried(self) -> bool:
        return self.field is not None


@dataclass(frozen=True, slots=True)
class ChoiceSpec:
    """One option-driven filter key: its grammar and its windows.

    ``kind`` selects the suffix grammar (``performance`` or ``volatility``);
    ``tokens`` reads the vocabulary from the catalogue instead of copying it, so
    the probe stays the one source of what Finviz accepts.
    """

    key: str
    kind: str
    grammar: str
    windows: tuple[Window, ...]

    @property
    def label(self) -> str:
        """The Finviz label, read from the catalogue instead of copied."""

        return FILTER_CATALOG[self.key].label

    @property
    def tokens(self) -> tuple[str, ...]:
        """The filter vocabulary: the catalogue's options minus panel controls."""

        return tuple(
            token
            for token in FILTER_CATALOG[self.key].options
            if token not in CONTROL_TOKENS
        )


#: The three-year window is in Finviz's dropdown and in no screen row. Naming the
#: column that would answer it (``ret_3y``, deep/quote path) keeps the refusal
#: actionable instead of "not supported".
_NO_THREE_YEAR = (
    "no screen row carries a three-year return: the parity scan requests "
    "Perf.W/1M/3M/6M/YTD/Y/5Y/10Y (`ret_1w` … `ret_10y`), and `ret_3y` exists "
    "only on the deep/quote path"
)

PERFORMANCE_WINDOWS: tuple[Window, ...] = (
    Window("d", "today", "change_pct"),
    Window("1w", "week", "ret_1w"),
    Window("4w", "month", "ret_1m"),
    Window("13w", "quarter", "ret_3m"),
    Window("26w", "half-year", "ret_6m"),
    Window("ytd", "year-to-date", "ret_ytd"),
    Window("52w", "year", "ret_1y"),
    Window("3y", "3-year", None, _NO_THREE_YEAR),
    Window("5y", "5-year", "ret_5y"),
    Window("10y", "10-year", "ret_10y"),
)

VOLATILITY_WINDOWS: tuple[Window, ...] = (
    Window("wo", "weekly volatility", "volatility_w"),
    Window("mo", "monthly volatility", "volatility_m"),
)

_PERFORMANCE_GRAMMAR = (
    "<window><direction>: windows d, 1w, 4w, 13w, 26w, ytd, 52w, 3y, 5y, 10y; "
    "directions up, down, <N>u (down at least N percent), <N>o (up at least N "
    "percent)"
)
_VOLATILITY_GRAMMAR = (
    "<wo|mo><N>: weekly (wo) or monthly (mo) volatility of at least N percent"
)

#: Key → spec, in catalogue order for the three keys that are choice filters.
CHOICES: Mapping[str, ChoiceSpec] = MappingProxyType(
    {
        "ta_perf": ChoiceSpec(
            "ta_perf", "performance", _PERFORMANCE_GRAMMAR, PERFORMANCE_WINDOWS
        ),
        "ta_perf2": ChoiceSpec(
            "ta_perf2",
            "performance",
            _PERFORMANCE_GRAMMAR,
            PERFORMANCE_WINDOWS,
        ),
        "ta_volatility": ChoiceSpec(
            "ta_volatility",
            "volatility",
            _VOLATILITY_GRAMMAR,
            VOLATILITY_WINDOWS,
        ),
    }
)
CHOICE_KEYS: tuple[str, ...] = tuple(CHOICES)


class ChoiceOptionError(ValueError):
    """An option that cannot be compiled, with a stable machine code (422)."""

    code = "invalid_filter_option"

    def __init__(self, key: object, message: str) -> None:
        super().__init__(message)
        self.key = key


class UnknownChoiceOption(ChoiceOptionError):
    """The token is not part of the key's Finviz vocabulary (or is a control)."""

    code = "unknown_filter_option"


class UnsupportedChoiceOption(ChoiceOptionError):
    """The token is Finviz's, but no row column can answer its window."""

    code = "unsupported_filter_option"


class NotChoiceFilter(ChoiceOptionError):
    """The key does not filter through options at all."""

    code = "not_a_choice_filter"


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def _spec(key: object) -> ChoiceSpec:
    """The choice spec for ``key``; every other key is refused by name."""

    if not isinstance(key, str) or key not in CHOICES:
        raise NotChoiceFilter(
            key,
            f"filter key {key!r} does not filter through options; it resolves to "
            "one row column (app.screen.filter_fields.binding). Option-driven "
            f"keys: {', '.join(CHOICE_KEYS)}",
        )
    return CHOICES[key]


def _unknown(spec: ChoiceSpec, token: object, hint: str = "") -> UnknownChoiceOption:
    return UnknownChoiceOption(
        spec.key,
        f"{spec.key} ({spec.label}) has no option {token!r}: the vocabulary is "
        f"Finviz's own and this key takes {len(spec.tokens)} tokens. {spec.grammar}. "
        "The full list, with the predicate each token compiles to, is published at "
        f"GET /screens/select/schema under x-filter-keys.{spec.key}.choice.options"
        + (f" {hint}" if hint else ""),
    )


def _split(spec: ChoiceSpec, token: str) -> tuple[Window, str]:
    """Split ``token`` into its window and suffix, longest window prefix first."""

    for window in sorted(spec.windows, key=lambda item: len(item.token), reverse=True):
        if token.startswith(window.token):
            return window, token[len(window.token) :]
    raise _unknown(spec, token)


def _compile(spec: ChoiceSpec, token: str) -> Filter:
    """The predicate one catalogue token compiles to, or a refusal by name."""

    window, suffix = _split(spec, token)
    if spec.kind == "performance":
        if suffix == "up":
            op, value = "gt", 0.0
        elif suffix == "down":
            op, value = "lt", 0.0
        else:
            matched = _THRESHOLD.match(suffix)
            if matched is None:
                raise _unknown(
                    spec,
                    token,
                    f"The direction {suffix!r} is not one of up, down, <N>u, <N>o.",
                )
            number = float(matched.group("number"))
            op, value = ("lte", -number) if matched.group("side") == "u" else ("gte", number)
    else:
        matched = _COUNT.match(suffix)
        if matched is None:
            raise _unknown(spec, token, f"The threshold {suffix!r} is not a number.")
        op, value = "gte", float(matched.group("number"))

    if window.field is None:
        raise UnsupportedChoiceOption(
            spec.key,
            f"{spec.key} ({spec.label}) option {token!r} asks for the {window.label} "
            f"window, but {window.missing}. Windows that do compile on this key: "
            f"{', '.join(item.token for item in spec.windows if item.is_carried)}",
        )
    return compile_filter({"field": window.field, "op": op, "value": value})


def _build_tables() -> tuple[dict[str, dict[str, Filter]], dict[str, dict[str, str]]]:
    """Every catalogue token of every choice key: compiled, or refused with why."""

    compiled: dict[str, dict[str, Filter]] = {}
    unsupported: dict[str, dict[str, str]] = {}
    for key, spec in CHOICES.items():
        accepted: dict[str, Filter] = {}
        refused: dict[str, str] = {}
        for token in spec.tokens:
            try:
                accepted[token] = _compile(spec, token)
            except UnsupportedChoiceOption as exc:
                refused[token] = str(exc)
            except UnknownChoiceOption as exc:  # pragma: no cover - boot failure
                raise RuntimeError(
                    f"{key}: the probe's token {token!r} does not parse: {exc}"
                ) from exc
        compiled[key] = accepted
        unsupported[key] = refused
    return compiled, unsupported


_BUILT, _REFUSED = _build_tables()

#: Key → token → the predicate it compiles to. Built once, at import, from the
#: catalogue's vocabulary; :func:`compile_option` never re-parses a known token
#: and never invents one.
COMPILED: Mapping[str, Mapping[str, Filter]] = MappingProxyType(
    {key: MappingProxyType(table) for key, table in _BUILT.items()}
)

#: Key → token → the sentence explaining why its window cannot be answered.
UNSUPPORTED: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {key: MappingProxyType(table) for key, table in _REFUSED.items()}
)


def _validate() -> None:
    """Fail at import: the tables, the catalogue and the rows must agree."""

    for key, spec in CHOICES.items():
        found = FILTER_CATALOG.get(key)
        if found is None:
            raise RuntimeError(f"choice key {key!r} is not in the Finviz catalogue")
        if not found.choice:
            raise RuntimeError(
                f"filters.FILTER_CATALOG[{key!r}].choice is False, but "
                "app.screen.choice_filters declares it an option-driven key"
            )
        if not spec.tokens:
            raise RuntimeError(f"choice key {key!r} has no filter vocabulary")

    declared = {key for key, spec in FILTER_CATALOG.items() if spec.choice}
    if declared != set(CHOICES):
        raise RuntimeError(
            "the choice keys drifted between filters.FILTER_CATALOG and "
            f"app.screen.choice_filters: catalogue={sorted(declared)}, "
            f"module={sorted(CHOICES)}"
        )

    rebuilt = _build_tables()
    current = (
        {key: dict(table) for key, table in COMPILED.items()},
        {key: dict(table) for key, table in UNSUPPORTED.items()},
    )
    if rebuilt != current:
        raise RuntimeError(
            "the compiled option tables drifted from the choice windows; "
            "re-import the module (a stale table would filter the wrong window)"
        )

    from app.screen import universe as parity

    for key, spec in CHOICES.items():
        seen: set[str] = set()
        for window in spec.windows:
            if not window.token or window.token in seen:
                raise RuntimeError(f"{key} has a duplicate or empty window {window.token!r}")
            seen.add(window.token)
            if (window.field is None) == (window.missing is None):
                raise RuntimeError(
                    f"{key}.{window.token} needs exactly one of field/missing"
                )
            tokens = [token for token in spec.tokens if token.startswith(window.token)]
            if not tokens:
                raise RuntimeError(
                    f"{key}.{window.token} is declared but no catalogue option uses it"
                )
            if window.field is None:
                if not all(token in UNSUPPORTED[key] for token in tokens):
                    raise RuntimeError(
                        f"{key}: a window without a column must refuse all its tokens"
                    )
                continue
            if window.field not in FIELDS or FIELDS[window.field] != "number":
                raise RuntimeError(
                    f"{key}.{window.token} names {window.field!r}, which is not a "
                    "numeric row field in app.screen.filters.FIELDS"
                )
            if window.field not in parity.CARRIED_FIELDS:
                raise RuntimeError(
                    f"{key}.{window.token} names {window.field!r}, which no parity "
                    "row carries (app.screen.universe.CARRIED_FIELDS)"
                )
            if not all(token in COMPILED[key] for token in tokens):
                raise RuntimeError(f"{key}: a carried window must compile all its tokens")

        if set(COMPILED[key]) | set(UNSUPPORTED[key]) != set(spec.tokens):
            raise RuntimeError(
                f"{key}: every catalogue token must be compiled or refused by name"
            )
        if any(token in CONTROL_TOKENS for token in spec.tokens):
            raise RuntimeError(f"{key}: a panel control token leaked into the vocabulary")


_validate()


# ---------------------------------------------------------------------------
# The public surface
# ---------------------------------------------------------------------------


def is_choice(key: object) -> bool:
    """Does ``key`` filter through its options?"""

    return isinstance(key, str) and key in CHOICES


def compile_option(key: object, option: object) -> Filter:
    """Compile one Finviz option token into a predicate over a real column.

    Raises :class:`UnknownChoiceOption` for a token outside the key's catalogue
    vocabulary (including the panel's ``""``/``modal`` controls),
    :class:`UnsupportedChoiceOption` for a token whose window no row carries,
    and :class:`NotChoiceFilter` for a key that is not option-driven.
    """

    spec = _spec(key)
    if not isinstance(option, str):
        raise _unknown(spec, option)
    if option in CONTROL_TOKENS:
        raise _unknown(
            spec, option, "That token is a panel control the probe recorded, not a bucket."
        )
    if option not in spec.tokens:
        raise _unknown(spec, option)
    return _compile(spec, option)


def options_for(key: object) -> tuple[str, ...]:
    """Every filter token the key accepts, in the probe's order (compiled first)."""

    return _spec(key).tokens


def choices_for(key: object) -> Mapping[str, Filter]:
    """Token → predicate, for the tokens this key can compile today."""

    return COMPILED[_spec(key).key]


def unsupported_for(key: object) -> Mapping[str, str]:
    """Token → why its window cannot be answered, for the refused tokens."""

    return UNSUPPORTED[_spec(key).key]


def fields_for(key: object) -> tuple[str, ...]:
    """The row columns the key's windows read, in window order, no duplicates."""

    fields: list[str] = []
    for window in _spec(key).windows:
        if window.field is not None and window.field not in fields:
            fields.append(window.field)
    return tuple(fields)


def describe(key: object) -> dict[str, object]:
    """The published form of one choice key: grammar, columns, every predicate.

    ``options`` maps each accepted token to the filter expression it compiles to
    (``"ret_1w lte -10.0"``) — the same ``field op value`` three-part expression
    :func:`app.screen.filters.parse_filters` accepts, so a client can hand the
    string straight back to the DSL and get the identical predicate. ``windows``
    names the column per window (``None`` where no row carries one) and
    ``unsupported`` explains, per window, why its tokens are refused. That is
    enough to validate a token, render the choice and understand a 422 without a
    second request and without guessing the grammar.
    """

    spec = _spec(key)
    return {
        "grammar": spec.grammar,
        "fields": list(fields_for(spec.key)),
        "windows": {window.token: window.field for window in spec.windows},
        "options": {
            token: f"{predicate.field} {predicate.op} {predicate.value}"
            for token, predicate in COMPILED[spec.key].items()
        },
        "unsupported": {
            window.token: window.missing
            for window in spec.windows
            if window.missing is not None
        },
    }


__all__ = [
    "CHOICES",
    "CHOICE_KEYS",
    "COMPILED",
    "CONTROL_TOKENS",
    "ChoiceOptionError",
    "ChoiceSpec",
    "NotChoiceFilter",
    "PERFORMANCE_WINDOWS",
    "UNSUPPORTED",
    "UnknownChoiceOption",
    "UnsupportedChoiceOption",
    "VOLATILITY_WINDOWS",
    "Window",
    "choices_for",
    "compile_option",
    "describe",
    "fields_for",
    "is_choice",
    "options_for",
    "unsupported_for",
]
