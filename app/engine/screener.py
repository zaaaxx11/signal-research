"""The local screen engine: filter, order and page canonical rows.

The engine's job here is small and deliberately boring: take the rows a screen
already has, apply the caller's filters, order them and cut one page. No
provider is called, no value is fetched, averaged or filled, and no catalog file
is read here beyond the vocabulary of canonical keys.

What a row is
-------------
A row is a mapping from a canonical field to a ``Cell`` or to a plain value.
A ``Cell`` is unwrapped to its ``value``; anything else is used as given. A
``Cell`` whose value is ``None`` is an absence, not a zero — the cell's
provenance already says why it is empty, and the screen must not overwrite that
with a number.

Unknown is not zero, and it is not "different"
---------------------------------------------
A missing key, ``None`` and a non-finite number (``nan``, ``inf``) never satisfy
a comparison. That includes ``ne``: a missing P/E is not evidence that the P/E
differs from ten, so ``pe ne 10`` must not return the rows that have no P/E at
all. Those rows are excluded from every operator, and they sort last.

Nothing is coerced
------------------
A value's kind is read from the value itself: ``number``, ``text``, ``bool`` or
``other``. A numeric string (``"1000"``) is **text**, not a number; a numeric
operator applied to text simply does not match that row, and ``contains`` only
searches text. The alternative — guessing that ``"1000"`` meant a quantity —
turns a provider's formatting error into a silent screen result, which is the
one failure mode this engine exists to prevent. Request *bounds* are stricter
still: a mapping must carry a real number for a numeric operator, while the
``"field op value"`` shorthand infers integers and decimals because every token
in an expression is text by construction.

Kinds are never mixed by an operator: ``1`` never equals ``True``, ``5.0`` never
equals ``"5"``, and ``ne`` only reports a difference between two values of the
same kind. Equality across kinds is not evidence.

Operators
---------
``eq``, ``ne`` compare within a kind; ``gt``, ``gte``, ``lt``, ``lte`` and
``between`` compare numbers only (``between`` is inclusive on both ends and its
bounds must be two numbers in increasing order); ``in`` matches a member of the
same kind; ``contains`` is a case-insensitive substring search over text. An
unknown operator is a :class:`ScreenError`, never a filter that silently matches
nothing.

The field vocabulary
--------------------
Filter and sort fields are validated. ``allowed_fields``, when supplied, is the
whole vocabulary and anything outside it is rejected — that is the strict mode
an API route should use with the catalog keys. When it is omitted, a field is
known if the catalog names it or if the row set carries it; a field that none of
those know is a typo, and a typo must be an error rather than an empty screen.
A catalog field the rows do not carry is still a legitimate target (a sort over
an empty column is a no-op, not a typo); a name nobody knows is not.

Sorting
-------
Sorting is stable and total: missing values are last in both directions, ties
break on ``ticker`` when the row has one and then on the row's canonical
``repr``, so the order of a screen never depends on the order the rows arrived
in. Text sorts case-insensitively with the original spelling as the tie-break,
numbers sort numerically, and a field that mixes kinds orders numbers, then
text, then booleans, then everything else instead of raising ``TypeError`` —
kind groups keep that order in both directions, and ``descending`` only orders
the values inside a group. ``sort=None`` keeps the input order.

Pagination
----------
``total`` is the number of rows that matched **before** the page was cut, and
``returned`` is the size of the page. ``limit`` must be between 1 and
:data:`MAX_LIMIT`; ``offset`` must be zero or more. Both are rejected rather
than clamped: a caller that asked for 0 or 10,000 rows has a bug, and silently
serving 1 or 500 would hide it. A page past the end is an empty tuple with the
real ``total``.

The audit trail
---------------
``ScreenResult.applied_filters`` is an ordered tuple of canonical descriptors,
one per filter, shaped ``"field op value"`` with the value rendered by ``repr``
(so a string is quoted and a range reads ``"price between (5, 10)"``). It is the
exact list that was applied — no filter is folded away, reordered or dropped.

Rows come back by identity, in the order the sort produced; the engine never
copies a row, so a caller keeps the original cells and their provenance.
"""

from __future__ import annotations

import difflib
import math
import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from app.engine.catalog import CATALOG_BY_KEY
from app.engine.model import Cell

#: One page is twenty rows by default (the display contract of a screen).
DEFAULT_LIMIT = 20

#: A page wider than this is a request bug, not a screen.
MAX_LIMIT = 500

#: Every operator the engine speaks, in alphabetical order for error messages.
OPERATORS: tuple[str, ...] = (
    "between",
    "contains",
    "eq",
    "gte",
    "gt",
    "in",
    "lte",
    "lt",
    "ne",
)
_OPERATOR_SET = frozenset(OPERATORS)

#: Operators that compare quantities.
NUMERIC_OPERATORS: frozenset[str] = frozenset({"between", "gt", "gte", "lt", "lte"})

#: The one operator that searches text.
TEXT_OPERATORS: frozenset[str] = frozenset({"contains"})

#: Symbolic spellings accepted in a filter expression or mapping.
_SYMBOLS: Mapping[str, str] = MappingProxyType(
    {"=": "eq", "==": "eq", "!=": "ne", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}
)

#: The canonical vocabulary a screen starts from. A row set may carry more (a
#: caller may screen over its own columns), but a typo is never one of them.
DEFAULT_FIELDS: frozenset[str] = frozenset(CATALOG_BY_KEY)

#: Value kinds. ``missing`` is the only one that never matches and sorts last.
_NUMBER = "number"
_TEXT = "text"
_BOOL = "bool"
_OTHER = "other"
_MISSING = "missing"

_EXPRESSION = re.compile(
    r"^\s*(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:(?P<symbol><=|>=|==|!=|=|<|>)|"
    r"(?P<name>contains|between|gte|lte|eq|ne|gt|lt|in)\b)\s*"
    r"(?P<value>.*?)\s*$",
    re.IGNORECASE,
)

#: ``(primary key tail, (ticker, repr) tie-break, row)`` inside one kind group.
_OrderEntry = tuple[tuple[object, ...], tuple[str, str], Mapping[str, object]]

_INTEGER = re.compile(r"^[+-]?\d+$")
_DECIMAL = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")


class ScreenError(ValueError):
    """A malformed screen request.

    ``code`` names the failing rule so an API layer can map it to a 4xx without
    parsing the message: ``unknown_field``, ``unknown_operator``,
    ``invalid_value`` or ``invalid_limit``. Data is never the reason for this
    exception — an unknown *value* is a non-match, not an error.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Value reading and typing
# ---------------------------------------------------------------------------


def _cell_value(raw: object) -> object:
    """Unwrap a ``Cell``; anything else is already the value."""

    if isinstance(raw, Cell):
        return raw.value
    return raw


def _kind(value: object) -> str:
    """Classify a value without converting it.

    Non-finite numbers are ``missing``: they compare false in every direction,
    so treating them as quantities would turn an unknown into a silent
    mismatch. ``bool`` is its own kind because ``True == 1`` in Python and a
    flag is not a quantity.
    """

    if value is None:
        return _MISSING
    if isinstance(value, bool):
        return _BOOL
    if isinstance(value, int):
        return _NUMBER
    if isinstance(value, float):
        return _NUMBER if math.isfinite(value) else _MISSING
    if isinstance(value, Decimal):
        return _NUMBER if value.is_finite() else _MISSING
    if isinstance(value, str):
        return _TEXT
    return _OTHER


def _field_value(row: Mapping[str, object], field: str) -> object:
    """The value a row carries for ``field``; ``None`` when it carries none.

    A mapping that does not have the key is indistinguishable from one that
    stores ``None`` here on purpose: both are unknown, and the screen treats
    unknown the same way everywhere.
    """

    return _cell_value(row.get(field))


# ---------------------------------------------------------------------------
# Request shapes
# ---------------------------------------------------------------------------


def _clean_field(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ScreenError(
            "unknown_field", f"a field must be a non-blank string, got {raw!r}"
        )
    return raw.strip()


def _allowed_names(allowed_fields: Collection[str]) -> frozenset[str]:
    """A strict field vocabulary; a bare string is a bug, not one field per letter."""

    if isinstance(allowed_fields, str):
        raise _invalid(
            "allowed_fields must be a collection of field names, not a string"
        )
    return frozenset(str(name) for name in allowed_fields)


def _operator(raw: object) -> str:
    """Normalise an operator token, or reject it by name."""

    if not isinstance(raw, str):
        raise ScreenError(
            "unknown_operator",
            f"unknown operator {raw!r}; available operators: {', '.join(OPERATORS)}",
        )
    token = raw.strip().casefold()
    token = _SYMBOLS.get(token, token)
    if token not in _OPERATOR_SET:
        raise ScreenError(
            "unknown_operator",
            f"unknown operator {raw!r}; available operators: {', '.join(OPERATORS)}",
        )
    return token


def _looks_like_operator(raw: object) -> bool:
    if not isinstance(raw, str):
        return False
    token = raw.strip().casefold()
    return _SYMBOLS.get(token, token) in _OPERATOR_SET


def _unquote(value: str) -> str:
    """Strip one pair of matching quotes from a scalar."""

    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _literal(text: str) -> object:
    """Infer an integer/decimal from an expression token; leave other text alone.

    Only the ``"field op value"`` shorthand uses this: every token there is a
    string, so ``market_cap gte 1000`` would otherwise compare text to numbers
    and never match. Typed mappings are not coerced.
    """

    token = text.strip()
    if _INTEGER.match(token):
        try:
            return int(token)
        except ValueError:  # pragma: no cover - an absurdly long digit string
            return token
    if _DECIMAL.match(token):
        try:
            return float(token)
        except ValueError:  # pragma: no cover - guarded by the regex
            return token
    return token


# ---------------------------------------------------------------------------
# Value coercion for one operator
# ---------------------------------------------------------------------------


def _invalid(message: str) -> ScreenError:
    return ScreenError("invalid_value", message)


def _number(field: str, raw: object) -> int | float | Decimal:
    """A finite number, or ``invalid_value``. Booleans are flags, not numbers."""

    if isinstance(raw, bool) or not isinstance(raw, (int, float, Decimal)):
        raise _invalid(f"{field} expects a number, got {raw!r}")
    if isinstance(raw, float) and not math.isfinite(raw):
        raise _invalid(f"{field} expects a finite number, got {raw!r}")
    if isinstance(raw, Decimal) and not raw.is_finite():
        raise _invalid(f"{field} expects a finite number, got {raw!r}")
    return raw


def _bounds(field: str, raw: object) -> tuple[int | float | Decimal, ...]:
    """The inclusive ``(low, high)`` pair of a ``between`` filter."""

    if isinstance(raw, str):
        parts: list[object] = [
            _literal(_unquote(part))
            for part in re.split(r"\s*(?:\.\.|,|:)\s*", raw.strip().strip("[]()"))
        ]
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        raise _invalid(f"{field} 'between' expects two numbers, got {raw!r}")
    if len(parts) != 2:
        raise _invalid(f"{field} 'between' expects exactly two numbers, got {raw!r}")
    low = _number(field, parts[0])
    high = _number(field, parts[1])
    if low > high:
        raise _invalid(f"{field} 'between' bounds are reversed: {low} > {high}")
    return (low, high)


def _members(field: str, raw: object) -> tuple[object, ...]:
    """The members of an ``in`` filter, as supplied, in supplied order."""

    if isinstance(raw, str):
        text = raw.strip()
        if len(text) >= 2 and text[0] in "([" and text[-1] in ")]":
            text = text[1:-1]
        parts: list[object] = (
            [_literal(_unquote(part)) for part in text.split(",")]
            if text.strip()
            else []
        )
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    elif isinstance(raw, (set, frozenset)):
        raise _invalid(
            f"{field} 'in' expects an ordered sequence of values, "
            f"got {type(raw).__name__}"
        )
    else:
        raise _invalid(f"{field} 'in' expects a sequence of values, got {raw!r}")
    if not parts:
        raise _invalid(f"{field} 'in' expects at least one value")
    return tuple(_scalar(field, part) for part in parts)


def _scalar(field: str, raw: object) -> object:
    """One comparable scalar: text, boolean or finite number."""

    if isinstance(raw, float) and not math.isfinite(raw):
        raise _invalid(f"{field} expects a finite scalar, got {raw!r}")
    if isinstance(raw, Decimal) and not raw.is_finite():
        raise _invalid(f"{field} expects a finite scalar, got {raw!r}")
    if isinstance(raw, (str, bool, int, float, Decimal)):
        return raw
    raise _invalid(f"{field} expects a text, boolean or numeric value, got {raw!r}")


def _contains(field: str, raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise _invalid(f"{field} 'contains' expects a non-empty string, got {raw!r}")
    return raw.strip()


def _coerce(field: str, op: str, raw: object) -> object:
    """Validate ``raw`` for ``op`` and return its canonical form."""

    if op == "between":
        return _bounds(field, raw)
    if op == "in":
        return _members(field, raw)
    if op in TEXT_OPERATORS:
        return _contains(field, raw)
    if op in NUMERIC_OPERATORS:
        return _number(field, raw)
    return _scalar(field, raw)  # eq, ne


# ---------------------------------------------------------------------------
# The three request value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FilterSpec:
    """One compiled predicate: ``row[field] op value``.

    Construction validates the operator and the value shape, so a spec that
    exists is always evaluable. Names the row set does not carry are caught by
    :func:`apply`, which is the only place that can see the vocabulary.
    """

    field: str
    op: str
    value: object

    def __post_init__(self) -> None:
        field = _clean_field(self.field)
        op = _operator(self.op)
        object.__setattr__(self, "field", field)
        object.__setattr__(self, "op", op)
        object.__setattr__(self, "value", _coerce(field, op, self.value))


@dataclass(frozen=True, slots=True)
class SortSpec:
    """Order a screen by one field; descending is the top-of-screen default."""

    field: str
    descending: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", _clean_field(self.field))
        if not isinstance(self.descending, bool):
            raise ScreenError(
                "invalid_value",
                f"sort descending must be a boolean, got {self.descending!r}",
            )


def _filters(raw: object) -> tuple[FilterSpec, ...]:
    """Normalise every documented filter form into a tuple of specs."""

    if raw is None:
        return ()
    if isinstance(raw, (FilterSpec, Mapping, str)) or _is_triple(raw):
        return (compile_filter(raw, allowed_fields=None),)
    if isinstance(raw, (set, frozenset)):
        raise _invalid("filters must be an ordered sequence, not a set")
    if isinstance(raw, Iterable):
        return tuple(compile_filter(item, allowed_fields=None) for item in raw)
    raise _invalid(f"cannot read filters from {type(raw).__name__}: {raw!r}")


def _sort(raw: object) -> SortSpec | None:
    """Normalise a sort spec: mapping, ``"field"``, ``"-field"`` or ``"+field"``."""

    if raw is None or isinstance(raw, SortSpec):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ScreenError("unknown_field", "a sort needs a field name")
        if text.startswith("-"):
            return SortSpec(text[1:], descending=True)
        if text.startswith("+"):
            return SortSpec(text[1:], descending=False)
        return SortSpec(text, descending=True)
    if isinstance(raw, Mapping):
        extra = set(raw) - {"field", "descending"}
        if extra:
            raise _invalid(
                f"a sort mapping has unknown keys: {sorted(map(str, extra))}"
            )
        if "field" not in raw:
            raise _invalid("a sort mapping needs a 'field'")
        return SortSpec(field=raw["field"], descending=raw.get("descending", True))
    raise _invalid(f"cannot read a sort from {type(raw).__name__}: {raw!r}")


def _limit(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ScreenError("invalid_limit", f"limit must be an integer, got {raw!r}")
    if not 1 <= raw <= MAX_LIMIT:
        raise ScreenError(
            "invalid_limit", f"limit must be between 1 and {MAX_LIMIT}, got {raw!r}"
        )
    return raw


def _offset(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ScreenError("invalid_limit", f"offset must be an integer, got {raw!r}")
    if raw < 0:
        raise ScreenError("invalid_limit", f"offset must be zero or more, got {raw!r}")
    return raw


@dataclass(frozen=True, slots=True)
class ScreenQuery:
    """One screen request: filters ANDed together, at most one sort, one page."""

    filters: tuple[FilterSpec, ...] = ()
    sort: SortSpec | None = None
    limit: int = DEFAULT_LIMIT
    offset: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "filters", _filters(self.filters))
        object.__setattr__(self, "sort", _sort(self.sort))
        object.__setattr__(self, "limit", _limit(self.limit))
        object.__setattr__(self, "offset", _offset(self.offset))


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """The page, the exact match count and the filters that produced them."""

    rows: tuple[Mapping[str, object], ...]
    total: int
    returned: int
    limit: int
    offset: int
    applied_filters: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(self, "applied_filters", tuple(self.applied_filters))


# ---------------------------------------------------------------------------
# Compile / parse
# ---------------------------------------------------------------------------


def _is_triple(raw: object) -> bool:
    return (
        isinstance(raw, (list, tuple))
        and len(raw) == 3
        and isinstance(raw[0], str)
        and _looks_like_operator(raw[1])
    )


def _expression(text: str) -> tuple[str, str, object]:
    """Split ``"field op value"`` into its three parts."""

    match = _EXPRESSION.match(text)
    if match is None or not match.group("value").strip():
        raise _invalid(
            f"cannot parse filter expression {text!r}; expected 'field op value', "
            "e.g. 'market_cap gte 100000000'"
        )
    op = _operator(match.group("symbol") or match.group("name") or "")
    value: object = _unquote(match.group("value"))
    if op not in TEXT_OPERATORS:
        value = _literal(value)
    return match.group("field"), op, value


def _unpack(raw: object) -> tuple[object, object, object]:
    """Accept every form a filter may arrive in and return its three parts."""

    if isinstance(raw, FilterSpec):
        return raw.field, raw.op, raw.value
    if isinstance(raw, str):
        return _expression(raw)
    if isinstance(raw, Mapping):
        extra = set(raw) - {"field", "op", "value"}
        if extra:
            raise _invalid(
                f"a filter mapping has unknown keys: {sorted(map(str, extra))}"
            )
        missing = [name for name in ("field", "op", "value") if name not in raw]
        if missing:
            raise _invalid(
                f"a filter mapping needs {', '.join(missing)}: {dict(raw)!r}"
            )
        return raw["field"], raw["op"], raw["value"]
    if isinstance(raw, (list, tuple)):
        if len(raw) != 3:
            raise _invalid(
                f"a filter triple is (field, op, value), got {len(raw)} items"
            )
        return raw[0], raw[1], raw[2]
    raise _invalid(f"cannot compile a filter from {type(raw).__name__}: {raw!r}")


def _require_field(field: str, vocabulary: frozenset[str]) -> None:
    """Reject a field the vocabulary does not know, suggesting a near miss."""

    if field in vocabulary:
        return
    matches = difflib.get_close_matches(field, sorted(vocabulary), n=3, cutoff=0.6)
    hint = (
        f"; did you mean {', '.join(repr(match) for match in matches)}?"
        if matches
        else ""
    )
    raise ScreenError(
        "unknown_field",
        f"unknown field {field!r}{hint} ({len(vocabulary)} fields are known; "
        "pass allowed_fields to screen over a different vocabulary)",
    )


def compile_filter(
    raw: object, *, allowed_fields: Collection[str] | None = DEFAULT_FIELDS
) -> FilterSpec:
    """Validate one filter and return it in canonical form.

    ``raw`` may be a :class:`FilterSpec`, a mapping with ``field``/``op``/
    ``value``, a ``(field, op, value)`` triple, or a ``"field op value"``
    expression. ``allowed_fields`` defaults to the catalog keys; pass ``None``
    to check only the operator and the value shape (``apply`` will check the
    field against the rows it is given).
    """

    field, op, value = _unpack(raw)
    spec = FilterSpec(field=field, op=op, value=value)  # type: ignore[arg-type]
    if allowed_fields is not None:
        _require_field(spec.field, _allowed_names(allowed_fields))
    return spec


def parse_query(payload: object = None) -> ScreenQuery:
    """Parse a wire-shaped query document into a :class:`ScreenQuery`.

    The document may carry ``filters``, ``sort``, ``limit`` and ``offset``;
    any other key is rejected rather than ignored, because a typo such as
    ``"fitlers"`` must not turn a filtered request into the whole universe.
    Values are validated here; field names are validated by :func:`apply`,
    where the row set and ``allowed_fields`` are known.
    """

    if isinstance(payload, ScreenQuery):
        return payload
    if payload is None:
        return ScreenQuery()
    if not isinstance(payload, Mapping):
        raise _invalid(
            f"a query payload must be a mapping, got {type(payload).__name__}"
        )

    extra = set(payload) - {"filters", "sort", "limit", "offset"}
    if extra:
        raise _invalid(f"a query payload has unknown keys: {sorted(map(str, extra))}")

    return ScreenQuery(
        filters=_filters(payload.get("filters")),
        sort=_sort(payload.get("sort")),
        limit=payload.get("limit", DEFAULT_LIMIT),
        offset=payload.get("offset", 0),
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _equal(value: object, expected: object) -> bool:
    """Equality inside one kind; across kinds nothing is equal."""

    kind = _kind(value)
    if kind != _kind(expected):
        return False
    if kind == _MISSING:
        return False
    return bool(value == expected)


def _matches(row: Mapping[str, object], spec: FilterSpec) -> bool:
    """Does one row satisfy one filter? Unknown values satisfy none."""

    value = _field_value(row, spec.field)
    kind = _kind(value)
    if kind == _MISSING:
        return False

    expected = spec.value
    op = spec.op

    if op == "eq":
        return _equal(value, expected)
    if op == "ne":
        return _kind(expected) == kind and not _equal(value, expected)
    if op in NUMERIC_OPERATORS:
        if kind != _NUMBER:
            return False
        if op == "between":
            low, high = expected  # type: ignore[misc]
            return bool(low <= value <= high)
        if op == "gt":
            return bool(value > expected)  # type: ignore[operator]
        if op == "gte":
            return bool(value >= expected)  # type: ignore[operator]
        if op == "lt":
            return bool(value < expected)  # type: ignore[operator]
        return bool(value <= expected)  # type: ignore[operator]
    if op == "in":
        members: tuple[object, ...] = expected  # type: ignore[assignment]
        return any(_equal(value, member) for member in members)
    if op == "contains":
        if kind != _TEXT:
            return False
        return str(expected).casefold() in str(value).casefold()
    return False  # pragma: no cover - every operator is handled above


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def _order_key(value: object) -> tuple[object, ...]:
    """A total, deterministic key for one value; kinds never compare directly."""

    kind = _kind(value)
    if kind == _NUMBER:
        try:
            return (0, float(value))  # type: ignore[arg-type]
        except (OverflowError, ValueError):  # pragma: no cover - an absurd magnitude
            return (3, repr(value))
    if kind == _TEXT:
        text = str(value)
        return (1, text.casefold(), text)
    if kind == _BOOL:
        return (2, 1 if value else 0)
    return (3, repr(value))


def _ticker(row: Mapping[str, object]) -> str:
    """The tie-break ticker, case-folded; blank when the row has none."""

    value = _field_value(row, "ticker")
    return value.casefold() if isinstance(value, str) else ""


def _fingerprint(row: Mapping[str, object]) -> str:
    """A canonical ``repr`` of the row, independent of dict insertion order."""

    return repr(tuple(sorted((str(key), repr(value)) for key, value in row.items())))


def _rank(key: tuple[object, ...]) -> int:
    """The kind rank of an order key; rank 3 is the ``other`` bucket."""

    head = key[0] if key else 3
    return head if isinstance(head, int) else 3


def _order(
    rows: list[Mapping[str, object]], sort: SortSpec | None
) -> list[Mapping[str, object]]:
    """Order ``rows`` by ``sort`` with missing values last and ties broken.

    Rows are grouped by the kind of the sort value first, and the groups are
    always emitted in kind order (numbers, text, booleans, other). Only the
    values *inside* a group are reversed by ``descending``: a direction cannot
    meaningfully compare a number to a word, so it must not decide whether the
    words come before the numbers.
    """

    if sort is None:
        return rows

    groups: dict[int, list[_OrderEntry]] = {}
    missing: list[tuple[tuple[str, str], Mapping[str, object]]] = []
    for row in rows:
        value = _field_value(row, sort.field)
        tie = (_ticker(row), _fingerprint(row))
        if _kind(value) == _MISSING:
            missing.append((tie, row))
            continue
        key = _order_key(value)
        groups.setdefault(_rank(key), []).append((key[1:], tie, row))

    ordered: list[Mapping[str, object]] = []
    for rank in sorted(groups):
        bucket = groups[rank]
        # Tie-break first, then a stable sort on the primary key: that keeps
        # the ticker/repr order ascending even when the primary sort is
        # descending, which a single reversed tuple comparison could not do.
        bucket.sort(key=lambda item: item[1])
        bucket.sort(key=lambda item: item[0], reverse=sort.descending)
        ordered.extend(item[2] for item in bucket)

    missing.sort(key=lambda item: item[0])
    return ordered + [item[1] for item in missing]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _descriptor(spec: FilterSpec) -> str:
    """The canonical ``field op value`` descriptor used in the audit trail."""

    return f"{spec.field} {spec.op} {spec.value!r}"


def _vocabulary(
    rows: tuple[Mapping[str, object], ...], allowed_fields: Collection[str] | None
) -> frozenset[str]:
    """The fields this screen may name.

    An explicit ``allowed_fields`` is authoritative. Otherwise the catalog keys
    and every field the rows actually carry are known; a name outside both is a
    typo, and a typo must fail instead of returning an empty screen.
    """

    if allowed_fields is not None:
        return _allowed_names(allowed_fields)
    known = set(DEFAULT_FIELDS)
    for row in rows:
        known.update(str(key) for key in row)
    return frozenset(known)


def _as_query(query: object) -> ScreenQuery:
    if isinstance(query, ScreenQuery):
        return query
    if query is None or isinstance(query, Mapping):
        return parse_query(query)
    raise _invalid(f"cannot read a query from {type(query).__name__}: {query!r}")


def apply(
    rows: Iterable[Mapping[str, object]],
    query: ScreenQuery | Mapping[str, object] | None,
    *,
    allowed_fields: Collection[str] | None = None,
) -> ScreenResult:
    """Filter, order and page ``rows`` according to ``query``.

    ``query`` may be a :class:`ScreenQuery` or a wire-shaped mapping. Rows are
    read once into a tuple, so a generator is safe; each row is returned by
    identity and never copied, averaged or filled.

    ``allowed_fields`` replaces the field vocabulary. When it is ``None`` a
    field is known if the catalog names it or one of the rows carries it.
    """

    screen = _as_query(query)
    materialized = tuple(rows)
    for index, row in enumerate(materialized):
        if not isinstance(row, Mapping):
            raise _invalid(
                f"row {index} is not a mapping, got {type(row).__name__}: {row!r}"
            )

    vocabulary = _vocabulary(materialized, allowed_fields)
    for spec in screen.filters:
        _require_field(spec.field, vocabulary)
    if screen.sort is not None:
        _require_field(screen.sort.field, vocabulary)

    matched = [
        row
        for row in materialized
        if all(_matches(row, spec) for spec in screen.filters)
    ]
    ordered = _order(matched, screen.sort)
    page = tuple(ordered[screen.offset : screen.offset + screen.limit])

    return ScreenResult(
        rows=page,
        total=len(matched),
        returned=len(page),
        limit=screen.limit,
        offset=screen.offset,
        applied_filters=tuple(_descriptor(spec) for spec in screen.filters),
    )


__all__ = [
    "DEFAULT_FIELDS",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "NUMERIC_OPERATORS",
    "OPERATORS",
    "TEXT_OPERATORS",
    "FilterSpec",
    "ScreenError",
    "ScreenQuery",
    "ScreenResult",
    "SortSpec",
    "apply",
    "compile_filter",
    "parse_query",
]
