"""Phase 7 parity — every column the old cockpit drew still has a home.

Why this test exists
--------------------
``ui/`` is the shipped vanilla client and it is frozen. ``research/phase7-parity.md``
proves key by key — and, for the filters and the logos, against the live process —
that the React app at ``/app`` did not lose a column when it replaced it. A parity
report is a snapshot; this file is the ratchet that keeps it true.

What it reads
-------------
* ``ui/app.js`` — ``COLUMN_META`` (every column key the old grid can draw, with the
  label and the formatter it drew it with) and ``SCREEN_VIEWS`` (the six named tabs;
  ``All columns`` is built from their union, so the union is the renderable set).
  The list is **static in the source** — no column key arrives from an API response
  — so parsing this file is the whole surface, not an approximation of it.
* ``ui/index.html`` — the ``#filterSort`` option list, for the sort half.
* ``app.screen.columns`` / ``app.screen.views`` / ``app.screen.filter_fields`` /
  ``app.screen.rank`` — the new surface: the 116-key catalogue, the nine views plus
  ``DETAIL_ONLY``/``all``, the 50 live filter bindings and the closed sort
  vocabulary.

The rule, in one sentence
-------------------------
Every key ``ui/app.js`` can render is either a catalogue key, or a name the
catalogue table resolves to one, or an entry in :data:`ALLOWED_GAPS` that states
what the user loses — and nothing else.

Three properties follow, and each is a test below:

1. **A key removed from the catalogue fails, naming the old column that used it.**
   For an alias the *targets* are re-checked against ``CATALOGUE_KEY_SET`` rather
   than trusting :func:`app.screen.columns.field_for`, because ``rsi_14`` would
   still "resolve" to its own row column after ``rsi`` disappeared — the column
   would render null and nobody would notice.
2. **A key added to the frozen client fails until it is classified**, and the key
   count is pinned, so deleting a block of ``COLUMN_META`` cannot make the test
   vacuous by shrinking its own input.
3. **An allowlisted gap cannot rot.** A gap that later becomes renderable fails
   with the instruction to delete its entry, so the allowlist never grows into the
   place where real losses hide.

The filter half is the same argument for the other control surface: the old
advanced-filter drawer sent five Finviz keys and the old sort select offered three
values, and a key the registry now refuses (422) is a **regression**, not a gap —
the user typed a filter and the screen came back unfiltered.

No HTTP here on purpose: a unit test that needs :8099 is a test that gets skipped.
The live half of the proof — 25 ``GET /logos/{symbol}`` samples and a 200-and-narrows
probe of each of the five filter keys — is recorded in the report.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from app.screen import columns as column_map
from app.screen import rank as ranking
from app.screen import views as screen_views
from app.screen.filter_fields import FilterKeyError, binding
from app.screen.filters import OPERATORS

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
APP_JS: Final[Path] = REPO_ROOT / "ui" / "app.js"
INDEX_HTML: Final[Path] = REPO_ROOT / "ui" / "index.html"

#: ``  price: ["Price", "price"],`` — one line per column the old grid can draw.
_META_ENTRY: Final[re.Pattern[str]] = re.compile(
    r'^  (?P<key>[A-Za-z0-9_]+): \["(?P<label>[^"]+)", "(?P<kind>[a-z_]+)"\],$',
    re.MULTILINE,
)
#: ``  overview: { label: "Overview", columns: ["price", ...] },`` — one line per tab.
_VIEW_ENTRY: Final[re.Pattern[str]] = re.compile(
    r'(?P<name>\w+): \{ label: "(?P<label>[^"]+)", columns: \[(?P<columns>[^\]]*)\] \}'
)
_COLUMN_TOKEN: Final[re.Pattern[str]] = re.compile(r'"([^"]+)"')
#: ``["filterCap", "cap", "gte", 1e9]`` — the old drawer's control/key/op/scale.
_FILTER_FIELD: Final[re.Pattern[str]] = re.compile(
    r'\["(?P<id>\w+)", "(?P<key>[a-z_]+)", "(?P<op>[a-z]+)", [0-9.eE+-]+\]'
)
#: ``{ key: "sec", op: "eq", value: sector }`` — the sector select, same drawer.
_SECTOR_FILTER: Final[re.Pattern[str]] = re.compile(
    r'\{ key: "(?P<key>[a-z_]+)", op: "(?P<op>[a-z]+)", value:'
)
#: ``<option value="market_cap">Market cap</option>`` inside ``#filterSort``.
_SORT_OPTION: Final[re.Pattern[str]] = re.compile(r'<option value="([a-z_]+)">')

#: The old client is frozen at this many column keys. Pinned so a deletion from
#: ``COLUMN_META`` shrinks the input set loudly instead of silently weakening every
#: assertion below — a parity test whose corpus can disappear is not a parity test.
EXPECTED_OLD_COLUMN_COUNT: Final[int] = 72

#: Keys the old grid drew that the new surface **cannot** answer, each with what the
#: user loses and who decided it. An entry is only legitimate while
#: :func:`app.screen.columns.field_for` returns ``None`` for it; the moment the
#: column becomes renderable ``test_an_allowlisted_gap_is_still_a_gap`` fails.
ALLOWED_GAPS: Final[dict[str, str]] = {
    "option_short": (
        "The old Ownership tab's one 'Shortable' text cell. Finviz collapsed two "
        "independent facts into it; the catalogue keeps them apart as 'optionable' "
        "and 'shortable' (both render in the Profile view, both filterable via "
        "sh_opt once a flag column exists), and app.screen.columns.ABSENT_COLUMNS "
        "records that one name cannot address two cells. Accepted by plan §6.1 "
        "(the ownership view carries the three short-side cells instead); the user "
        "loses the single combined word, not the two facts — which the old UI never "
        "showed separately because it only ever had the composite."
    ),
}


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def old_columns() -> dict[str, tuple[str, str]]:
    """``{key: (label, formatter)}`` for every column ``ui/app.js`` can draw.

    The block is ``const COLUMN_META = { ... };`` at module scope; a malformed entry
    is not skipped — :func:`test_the_legacy_column_list_parses_cleanly` fails on the
    count, so a parser that stops matching cannot pass by finding nothing.
    """

    block = _source(APP_JS).split("const COLUMN_META = {", 1)[1].split("\n};", 1)[0]
    return {
        match.group("key"): (match.group("label"), match.group("kind"))
        for match in _META_ENTRY.finditer(block)
    }


def old_view_columns() -> dict[str, tuple[str, ...]]:
    """``{tab: columns}`` for the six named tabs the old client could switch to.

    ``SCREEN_VIEWS.all`` is defined *after* the literal (line 91) as the deduped
    union of these six, so it is not parsed — it is derived, and asserting the union
    covers every tab is what makes "72 keys, 71 renderable" a measured number.
    """

    block = _source(APP_JS).split("const SCREEN_VIEWS = {", 1)[1].split("\n};", 1)[0]
    return {
        match.group("name"): tuple(_COLUMN_TOKEN.findall(match.group("columns")))
        for match in _VIEW_ENTRY.finditer(block)
    }


def old_filter_keys() -> dict[str, str]:
    """``{key: operator}`` for the five keys the old advanced-filter drawer sends."""

    source = _source(APP_JS)
    drawer = source.split("function filterFields()", 1)[1].split("\n}", 1)[0]
    keys = {
        match.group("key"): match.group("op") for match in _FILTER_FIELD.finditer(drawer)
    }
    apply_block = source.split("function applyFilters()", 1)[1].split("\n}", 1)[0]
    for match in _SECTOR_FILTER.finditer(apply_block):
        keys[match.group("key")] = match.group("op")
    return keys


def old_sort_options() -> tuple[str, ...]:
    """The values the old ``#filterSort`` select offered, in order."""

    source = _source(INDEX_HTML)
    select = source.split('id="filterSort"', 1)[1].split("</select>", 1)[0]
    return tuple(_SORT_OPTION.findall(select))


def classify(key: str) -> tuple[str, tuple[str, ...]]:
    """``(kind, catalogue targets)`` for one old column key.

    ``kind`` is ``"catalogue"``, ``"alias"``, ``"gap"`` or ``"unmapped"``. A gap
    answers no target — that is what makes it a gap. ``"unmapped"`` is the shape a
    *composite* has when nobody allowlisted it: :func:`catalogue_keys_for` will hand
    back the two keys it stands for (``option_short`` → ``optionable``+``shortable``),
    but no row field answers the name itself, so a view rendering it draws ``null``.
    Resolving is not the same as rendering, which is why the alias path below
    insists on a row field.
    """

    if key in ALLOWED_GAPS:
        return "gap", ()
    if key in column_map.CATALOGUE_KEY_SET:
        return "catalogue", (key,)
    if column_map.field_for(key) is None:
        return "unmapped", ()
    return "alias", column_map.catalogue_keys_for(key)


OLD_COLUMNS: Final[dict[str, tuple[str, str]]] = old_columns()
OLD_VIEW_COLUMNS: Final[dict[str, tuple[str, ...]]] = old_view_columns()


def test_the_legacy_column_list_parses_cleanly() -> None:
    """The parser found the frozen corpus, not an empty block.

    Two failure modes, both loud: a ``COLUMN_META`` edit that changes the count, and
    a parser refactor that stops matching (which would make every parametrised case
    below vacuous).
    """

    assert APP_JS.is_file(), f"the frozen legacy client is missing at {APP_JS}"
    assert len(OLD_COLUMNS) == EXPECTED_OLD_COLUMN_COUNT, (
        f"ui/app.js declares {len(OLD_COLUMNS)} column keys, the parity proof "
        f"classified {EXPECTED_OLD_COLUMN_COUNT}. If the legacy client really "
        "changed, re-run the mapping in research/phase7-parity.md, classify any new "
        "key, and update EXPECTED_OLD_COLUMN_COUNT with it."
    )
    assert len(OLD_VIEW_COLUMNS) == 6, sorted(OLD_VIEW_COLUMNS)


def test_every_renderable_old_column_has_metadata() -> None:
    """A tab may only draw a key ``COLUMN_META`` knows — no unlabelled cell."""

    drawn = {key for columns in OLD_VIEW_COLUMNS.values() for key in columns}
    assert not drawn - set(OLD_COLUMNS), (
        "ui/app.js renders column(s) with no COLUMN_META entry: "
        f"{sorted(drawn - set(OLD_COLUMNS))}"
    )


@pytest.mark.parametrize("key", sorted(OLD_COLUMNS))
def test_every_old_column_is_mapped_or_a_documented_gap(key: str) -> None:
    """One case per old column: catalogue key, alias to one, or an allowed gap.

    The failure message names the key, the label and the tab, because "parity broke"
    is not actionable and "``perf_week`` (1W, performance tab) no longer resolves"
    is.
    """

    label, _kind = OLD_COLUMNS[key]
    tabs = ",".join(name for name, cols in OLD_VIEW_COLUMNS.items() if key in cols)
    where = f"{key!r} ({label}, tab: {tabs or 'none'})"

    kind, targets = classify(key)

    if kind == "gap":
        assert column_map.field_for(key) is None, (
            f"{where} is allowlisted as a gap but the catalogue now resolves it to "
            f"{column_map.field_for(key)!r}; delete the ALLOWED_GAPS entry"
        )
        assert ALLOWED_GAPS[key].strip(), f"{where} is a gap with no stated reason"
        return

    if kind == "unmapped":
        assert column_map.field_for(key) is None
        raise AssertionError(
            f"{where} names no single row field, so any view that renders it draws "
            f"null (a composite such as {key!r} resolves to "
            f"{column_map.catalogue_keys_for(key)} but no cell answers the name). "
            "Either give it a column or add it to ALLOWED_GAPS stating what the "
            "user loses."
        )

    assert targets, (
        f"{where} is neither a catalogue key nor an alias of one, and it is not in "
        "ALLOWED_GAPS. The new surface dropped a column the old UI could show: add "
        "the key to the catalogue (app/screen/columns.py + a view in "
        "app/screen/views.py), or add it to ALLOWED_GAPS with what the user loses."
    )

    unknown = [target for target in targets if target not in column_map.CATALOGUE_KEY_SET]
    assert not unknown, (
        f"{where} resolves to {unknown}, which is not in the 116-key catalogue. A "
        "key was removed or renamed without leaving the old name a target: either "
        "restore it or record the loss in ALLOWED_GAPS."
    )
    for target in targets:
        assert target in screen_views.CATALOG_KEY_SET, target
        # A catalogue key has exactly one home: a canonical view, or DETAIL_ONLY
        # (rendered by the all-columns destination). Neither is "nowhere".
        homes = {view.name for view in screen_views.VIEWS if target in view.columns}
        assert homes or target in screen_views.DETAIL_ONLY, (
            f"{where} → {target!r} has no home: not in a canonical view and not in "
            "DETAIL_ONLY, so no new surface can render it"
        )


def test_the_old_column_classes_add_up() -> None:
    """Report and test agree on the arithmetic a reviewer checks first."""

    classes: dict[str, int] = {"catalogue": 0, "alias": 0, "gap": 0, "unmapped": 0}
    for key in OLD_COLUMNS:
        classes[classify(key)[0]] += 1

    assert classes == {"catalogue": 48, "alias": 23, "gap": 1, "unmapped": 0}, classes
    assert sum(classes.values()) == len(OLD_COLUMNS) == EXPECTED_OLD_COLUMN_COUNT


def test_an_allowlisted_gap_is_still_a_gap() -> None:
    """No stale allowlist entries: every gap is a real gap and a real old column."""

    stale = sorted(key for key in ALLOWED_GAPS if column_map.field_for(key) is not None)
    assert not stale, (
        f"ALLOWED_GAPS entry/entries {stale} now resolve to a row field; a gap that "
        "is no longer a gap must be deleted, not left to rot"
    )
    dead = sorted(set(ALLOWED_GAPS) - set(OLD_COLUMNS))
    assert not dead, (
        f"ALLOWED_GAPS names {dead}, which ui/app.js never renders: an allowlist "
        "entry for a column nobody shows is a place for a real loss to hide"
    )
    for key, reason in ALLOWED_GAPS.items():
        assert column_map.absent_reason(key), (
            f"{key!r} is allowlisted but app.screen.columns.ABSENT_COLUMNS has no "
            "reason for it; the gap is undocumented on both sides"
        )


@pytest.mark.parametrize("key", sorted(old_filter_keys()))
def test_every_old_filter_key_is_still_a_live_registry_key(key: str) -> None:
    """A key the old drawer sent must filter, not return 422.

    This is the regression the report distinguishes from a gap: an unavailable key
    answers the unfiltered screen in the old client's eyes (it never checked), so a
    user who typed "P/E max 25" would read 5,576 rows as "every stock is cheap".
    """

    try:
        found = binding(key)
    except FilterKeyError as error:  # UnknownFilterKey / UnavailableFilterKey
        raise AssertionError(
            f"the old Advanced-filters drawer sends {key!r}, and the registry refuses "
            f"it: {error}. That is a filter regression (422), not a documented gap."
        ) from error
    assert found.is_available, (
        f"the old drawer sends {key!r} and the registry has no column for it: "
        f"{found.reason}"
    )
    assert found.field or found.choice


def test_the_old_filter_operators_are_in_the_dsl() -> None:
    """Every operator the drawer used is one the DSL compiles."""

    ops = set(old_filter_keys().values())
    assert ops, "the filter drawer parser found no controls"
    assert ops <= OPERATORS, sorted(ops - OPERATORS)


def test_the_old_sort_select_is_the_registry_sort_vocabulary() -> None:
    """The three sorts the old select offered are exactly the three the API serves.

    ``ui/index.html`` offers market_cap/turnover/gainers; ``app.screen.rank.SORTS``
    is the closed vocabulary of ``POST /screens/select``. Equality (not subset) is
    the assertion: a sort added on one side only is either dead UI or a 422.
    """

    assert old_sort_options() == ranking.SORTS
