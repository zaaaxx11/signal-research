"""The nine-view contract: every column a view names is a catalogue key.

Gate (``.hermes/plans/2026-10-30_ui-interface.md`` §2 task 0.6, §6.1, §6.2):

* ``VIEWS`` is the operator's nine views — today, value, quality, growth,
  momentum, trend, ownership, income, profile — with hand-written ordered
  column tuples, and every name in them is a key of the 116-column catalogue
  (``app/engine/data/finviz_columns_116.json``). A view may not name a name the
  catalogue does not have: that is how 23 columns ended up permanently blank
  while 41 % of the surface looked covered.
* the nine views plus :data:`app.screen.views.DETAIL_ONLY` **partition** the
  catalogue: 116 keys, each assigned exactly once, zero unknown, zero unassigned.
  ``all`` is the same 116 keys in catalogue order, never a hand-written list.
* the vanilla client (``ui/app.js``, ``ui/index.html``) is still served: its five
  renamed tabs keep their frozen column tuples (``ownership`` keeps its name and
  is served the canonical set), and every column name the client sends or renders
  resolves to a catalogue key — through the catalogue itself or through the alias
  table that records its successor. A name with no successor is a phantom, and
  this file fails on one.

The catalogue file is read here rather than through the module under test, so
the invariant is checked against the oracle instead of against a copy of it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.market import fields as F
from app.screen import views as V
from tests._support import ROOT

CATALOG_PATH = ROOT / "app" / "engine" / "data" / "finviz_columns_116.json"
CATALOG_ROWS = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
CATALOG_KEYS: tuple[str, ...] = tuple(str(row["key"]) for row in CATALOG_ROWS)

#: The nine views, in the order the plan lists them (plan §6.1).
NINE_VIEWS: tuple[str, ...] = (
    "today",
    "value",
    "quality",
    "growth",
    "momentum",
    "trend",
    "ownership",
    "income",
    "profile",
)

#: Read defensively: until the rewrite declares it the RED run must report the
#: real unknown/unassigned counts instead of dying on an AttributeError.
DETAIL_ONLY: tuple[str, ...] = tuple(getattr(V, "DETAIL_ONLY", ()))

QUOTE_FIXTURE = ROOT / "tests" / "fixtures" / "finviz_quote_metrics.json"
SNAPSHOT = F.parse_snapshot(json.loads(QUOTE_FIXTURE.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Vocabulary: the nine views, in catalogue keys
# ---------------------------------------------------------------------------


def test_the_nine_views_are_the_operator_set() -> None:
    assert [view.name for view in V.VIEWS] == list(NINE_VIEWS)
    assert V.VIEW_NAMES == NINE_VIEWS
    assert V.ALL_VIEW_NAME == "all"
    assert V.CANONICAL_VIEW_NAMES == NINE_VIEWS + ("all",)


def test_every_view_has_a_label_and_the_job_it_answers() -> None:
    labels: set[str] = set()
    for view in V.VIEWS:
        assert view.label and view.label not in labels, f"{view.name} needs a distinct label"
        labels.add(view.label)
        assert view.job.endswith("?"), (
            f"{view.name} must state the one question it answers, as a question; got {view.job!r}"
        )
        assert isinstance(view.columns, tuple) and len(view.columns) >= 5, (
            f"{view.name} must own a hand-written tuple of at least five keys"
        )


def test_every_view_column_is_a_catalogue_key() -> None:
    unknown = [
        (view.name, key) for view in V.VIEWS for key in view.columns if key not in CATALOG_KEYS
    ]
    assert unknown == [], (
        "a view may only name a key the 116-column catalogue declares; these do not exist "
        f"and would render blank forever: {unknown}"
    )


def test_the_module_catalogue_is_the_json_file_in_order() -> None:
    assert V.CATALOG_KEYS == CATALOG_KEYS
    assert len(V.CATALOG_KEYS) == 116


def test_detail_only_keys_are_the_sixteen_the_plan_sets_aside() -> None:
    assert DETAIL_ONLY == (
        "eps_next_5y",
        "sales_past_3y",
        "sales_past_5y",
        "eps_surprise",
        "revenue_surprise",
        "all_time_low",
        "candlestick_pattern",
        "week_52_range",
        "news_url",
        "news_title",
        "news_time",
        "daily_digest",
        "website",
        "ticker",
        "sort_order",
        "row_type",
    )
    homeless = sorted(set(DETAIL_ONLY) - set(CATALOG_KEYS))
    assert homeless == [], f"detail-only names that are not catalogue keys: {homeless}"


def test_the_nine_views_and_the_detail_only_keys_partition_the_catalogue() -> None:
    """The invariant, stated once: 116 keys, each assigned exactly once.

    One assertion, because a broken partition is one bug: the failure names every
    unknown column (with the views that carry it), every duplicated key and every
    catalogue key that no view and no detail slot claims. A count alone would
    hide which 23 names were invented and which 68 keys were orphaned.
    """

    owner: dict[str, list[str]] = {}
    unknown: dict[str, list[str]] = {}

    for view in V.VIEWS:
        for key in view.columns:
            if key not in CATALOG_KEYS:
                unknown.setdefault(key, []).append(view.name)
                continue
            owner.setdefault(key, []).append(view.name)

    duplicated = {key: names for key, names in owner.items() if len(names) > 1}
    assigned = set(owner) | set(DETAIL_ONLY)
    overlap = sorted(set(owner) & set(DETAIL_ONLY))
    unassigned = [key for key in CATALOG_KEYS if key not in assigned]
    foreign = sorted(assigned - set(CATALOG_KEYS))

    problems: list[str] = []
    if unknown:
        problems.append(
            f"unknown columns ({len(unknown)}): "
            + ", ".join(f"{key} in {'/'.join(names)}" for key, names in sorted(unknown.items()))
        )
    if duplicated:
        problems.append(
            f"keys with more than one home ({len(duplicated)}): "
            + ", ".join(f"{key} in {'/'.join(names)}" for key, names in sorted(duplicated.items()))
        )
    if unassigned:
        problems.append(f"catalogue keys with no home ({len(unassigned)}): {', '.join(unassigned)}")
    if overlap:
        problems.append(f"keys both in a view and detail-only ({len(overlap)}): {', '.join(overlap)}")
    if foreign:
        problems.append(f"assigned names outside the catalogue ({len(foreign)}): {', '.join(foreign)}")
    if not problems:
        assert len(assigned) == len(CATALOG_KEYS) == 116, (
            f"the partition must cover 116 keys exactly once, got {len(assigned)}"
        )
    assert problems == [], "the nine views must partition the catalogue:\n  " + "\n  ".join(
        problems
    )


def test_all_is_every_catalogue_key_in_catalogue_order() -> None:
    assert V.view("all").columns == CATALOG_KEYS
    assert V.ALL_COLUMNS == CATALOG_KEYS
    assert len(V.ALL_COLUMNS) == len(set(V.ALL_COLUMNS)) == 116


def test_view_catalogue_is_the_metadata_the_ui_fetches() -> None:
    payload = V.view_catalogue()
    assert [entry["name"] for entry in payload] == list(V.CANONICAL_VIEW_NAMES)

    for entry in payload:
        assert set(entry) == {"name", "label", "job", "columns"}, (
            "a client must be able to render a tab from this payload alone"
        )
        assert isinstance(entry["columns"], list), "JSON has arrays, not tuples"
        assert entry["columns"] == list(V.VIEW_COLUMNS[str(entry["name"])])
        assert entry["label"] and entry["job"].endswith("?")

    # The nine views cover the 100 screener columns; detail-only keys are never
    # offered as a column, so a client cannot build a 116-column default table.
    shown = [key for entry in payload[:-1] for key in entry["columns"]]
    assert len(shown) == 100 and len(set(shown)) == 100
    assert set(shown) == set(CATALOG_KEYS) - set(DETAIL_ONLY)
    assert json.loads(json.dumps(payload)) == payload


@pytest.mark.parametrize(
    ("name", "columns"),
    [
        (
            "today",
            (
                "price",
                "change",
                "change_from_open",
                "gap",
                "prev_close",
                "open",
                "high",
                "low",
                "volume",
                "average_volume",
                "relative_volume",
            ),
        ),
        (
            "value",
            (
                "market_cap",
                "enterprise_value",
                "pe",
                "forward_pe",
                "peg",
                "ps",
                "pb",
                "pc",
                "pfcf",
                "ev_ebitda",
                "ev_sales",
                "target_price",
                "recommendation",
            ),
        ),
        (
            "quality",
            (
                "roa",
                "roe",
                "roic",
                "current_ratio",
                "quick_ratio",
                "lt_debt_to_equity",
                "debt_to_equity",
                "book_per_share",
                "cash_per_share",
                "gross_margin",
                "operating_margin",
                "net_margin",
            ),
        ),
        (
            "growth",
            (
                "eps",
                "net_income",
                "sales",
                "eps_next_q",
                "eps_this_y",
                "eps_next_y",
                "eps_past_3y",
                "eps_past_5y",
                "eps_qoq",
                "sales_qoq",
                "eps_yoy_ttm",
                "sales_yoy_ttm",
            ),
        ),
        (
            "momentum",
            (
                "perf_1w",
                "perf_4w",
                "perf_13w",
                "perf_26w",
                "perf_52w",
                "perf_ytd",
                "perf_3y",
                "perf_5y",
                "perf_10y",
                "rsi",
            ),
        ),
        (
            "trend",
            (
                "sma20",
                "sma50",
                "sma200",
                "high_50d",
                "low_50d",
                "high_52w",
                "low_52w",
                "atr",
                "beta",
                "volatility_1w",
                "volatility_4w",
                "all_time_high",
            ),
        ),
        (
            "ownership",
            (
                "shares_outstanding",
                "shares_float",
                "float_pct",
                "insider_own",
                "insider_trans",
                "inst_own",
                "inst_trans",
                "short_float",
                "short_ratio",
                "short_interest",
            ),
        ),
        (
            "income",
            (
                "dividend",
                "dividend_yield",
                "dividend_ttm",
                "payout_ratio",
                "dividend_ex_date",
                "dividend_growth_1y",
                "dividend_growth_3y",
                "dividend_growth_5y",
            ),
        ),
        (
            "profile",
            (
                "company",
                "sector",
                "industry",
                "security_type",
                "country",
                "exchange",
                "index_membership",
                "optionable",
                "shortable",
                "employees",
                "earnings_date",
                "ipo_date",
            ),
        ),
    ],
)
def test_the_columns_of_each_view_are_pinned(name: str, columns: tuple[str, ...]) -> None:
    assert V.columns(name) == columns, (
        f"{name} lost, gained or reordered a column; a view is a contract, so the "
        "diff has to be deliberate"
    )


def test_no_view_names_a_column_twice() -> None:
    for view in V.VIEWS:
        assert len(set(view.columns)) == len(view.columns), f"{view.name} repeats a column"


def test_an_unknown_view_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="unknown view"):
        V.view("summary")
    with pytest.raises(ValueError, match="unknown view"):
        V.build_view("summary", {})
    with pytest.raises(ValueError, match="unknown view"):
        V.fill_rate("summary", {})


def test_a_view_may_not_repeat_a_column() -> None:
    with pytest.raises(RuntimeError, match="repeats a column"):
        V.ViewDefinition(
            name="broken", label="Broken", job="What?", columns=("pe", "pe"), description=""
        )


def test_the_column_map_is_the_definitions() -> None:
    for view in V.VIEWS:
        assert V.VIEW_COLUMNS[view.name] == view.columns
        assert V.VIEW_BY_NAME[view.name] is view
    assert V.VIEW_COLUMNS["all"] == V.ALL_COLUMNS


# ---------------------------------------------------------------------------
# The vanilla client keeps its vocabulary — by alias, not by phantom
# ---------------------------------------------------------------------------


def test_the_legacy_tabs_are_frozen_so_the_old_client_still_renders() -> None:
    assert [view.name for view in V.LEGACY_VIEWS] == [
        "overview",
        "valuation",
        "performance",
        "financial",
        "technical",
    ]
    assert set(view.name for view in V.LEGACY_VIEWS) <= set(V.SCREEN_VIEW_NAMES)
    assert set(view.name for view in V.LEGACY_VIEWS).isdisjoint(view.name for view in V.VIEWS)

    # Frozen: the response shape the shipped ui/app.js was written against.
    assert V.columns("valuation") == (
        "market_cap",
        "enterprise_value",
        "pe",
        "forward_pe",
        "peg",
        "ps",
        "pb",
        "pc",
        "pfcf",
        "ev_ebitda",
        "ev_sales",
    )
    assert "perf_week" in V.columns("performance")
    assert "eps_ttm" in V.columns("financial")
    assert "avg_volume" in V.columns("technical")
    assert "index" in V.columns("overview")


@pytest.mark.parametrize(
    ("name", "columns"),
    [
        (
            "overview",
            (
                "price",
                "change_pct",
                "volume",
                "market_cap",
                "pe",
                "forward_pe",
                "eps_ttm",
                "eps_next_y",
                "sales",
                "ps",
                "roe",
                "week52_high",
                "week52_low",
                "perf_ytd",
                "perf_year",
                "rsi_14",
                "atr_14",
                "rel_volume",
                "beta",
                "shares_outstanding",
                "shares_float",
                "insider_own",
                "inst_own",
                "employees",
                "ipo",
                "index",
                "option_short",
            ),
        ),
        (
            "performance",
            (
                "perf_week",
                "perf_month",
                "perf_quarter",
                "perf_half_y",
                "perf_ytd",
                "perf_year",
                "perf_3y",
                "perf_5y",
                "perf_10y",
                "week52_high",
                "week52_low",
            ),
        ),
        (
            "financial",
            (
                "income",
                "sales",
                "book_per_share",
                "cash_per_share",
                "eps_ttm",
                "eps_next_y",
                "eps_next_q",
                "eps_this_y",
                "eps_next_y_growth",
                "eps_next_5y",
                "eps_past_3_5y",
                "sales_past_3_5y",
                "eps_yoy_ttm",
                "sales_yoy_ttm",
                "eps_qoq",
                "sales_qoq",
                "roa",
                "roe",
                "roic",
                "gross_margin",
                "oper_margin",
                "profit_margin",
                "quick_ratio",
                "current_ratio",
                "debt_to_equity",
                "lt_debt_to_equity",
                "employees",
                "ipo",
            ),
        ),
        (
            "technical",
            (
                "price",
                "prev_close",
                "change_pct",
                "volume",
                "avg_volume",
                "rel_volume",
                "atr_14",
                "rsi_14",
                "sma20",
                "sma50",
                "sma200",
                "volatility",
                "beta",
            ),
        ),
    ],
)
def test_the_frozen_legacy_tab_shapes_are_pinned(name: str, columns: tuple[str, ...]) -> None:
    """These tuples are a wire contract with ``ui/app.js`` — pin them, don't trust them."""

    assert V.columns(name) == columns


async def test_the_vanilla_client_still_gets_its_tabs_from_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rename is server-side; the shipped client's requests still answer 200."""

    from tests import _screen as S
    from tests.test_selection_views import parity_web, selection_body

    for tab, expected in (
        ("valuation", V.columns("valuation")),
        ("overview", V.columns("overview")),
        ("all", V.ALL_COLUMNS),
    ):
        async with S.screen_client(tmp_path / tab, monkeypatch, web=parity_web()) as harness:
            response = await harness.client.post("/screens/select", json=selection_body(view=tab))

        assert response.status_code == 200, response.text
        row = response.json()["rows"][0]
        assert set(row) == {"rank", "symbol", "name", "as_of", "logo"} | set(expected), (
            f"the {tab} response lost or gained a key the shipped client reads"
        )


def test_ownership_kept_its_name_but_not_its_old_shape() -> None:
    """One tab name is shared: the canonical view wins, and it is a superset.

    The Finviz tab was insider-led and collapsed ``option_short`` into one cell;
    the canonical view leads with the share counts and carries the three
    short-side cells. Five of the client's six other names are read straight off
    the canonical row, and ``option_short`` is the one cell it renders blank.
    """

    assert "ownership" in V.SCREEN_VIEW_NAMES
    assert "ownership" not in [view.name for view in V.LEGACY_VIEWS]
    assert V.columns("ownership") == V.VIEW_COLUMNS[V.VIEW_NAMES[6]]
    old = {
        "insider_own",
        "insider_trans",
        "inst_own",
        "inst_trans",
        "shares_outstanding",
        "shares_float",
    }
    assert old <= set(V.columns("ownership"))
    assert "option_short" not in V.columns("ownership")
    assert V.resolve_column("option_short") == ("optionable", "shortable")


def test_every_legacy_column_names_its_catalogue_successor() -> None:
    """A legacy name is allowed to exist only while it points at a real key."""

    unresolved: list[tuple[str, str]] = []
    for view in V.LEGACY_VIEWS:
        for column in view.columns:
            if column in CATALOG_KEYS:
                continue
            targets = V.LEGACY_COLUMN_ALIASES.get(column)
            if not targets:
                unresolved.append((view.name, column))
    assert unresolved == [], (
        "these names the old client renders have no catalogue successor, so they are "
        f"phantoms again: {unresolved}"
    )

    unknown_targets = sorted(
        {target for targets in V.LEGACY_COLUMN_ALIASES.values() for target in targets}
        - set(CATALOG_KEYS)
    )
    assert unknown_targets == [], f"alias targets that are not catalogue keys: {unknown_targets}"


def test_the_twenty_three_phantoms_map_exactly_as_the_plan_says() -> None:
    """The compatibility table is the resolver's inverse, and it says so.

    The old version of this test pinned a hand-written dict, so a *wrong* target
    passed as long as it was written twice. The table is derived now; what has to
    be true is that each of the 23 shipped-client names still lands on the
    catalogue key it always meant.
    """

    assert V.LEGACY_COLUMN_ALIASES == {
        "atr_14": ("atr",),
        "avg_volume": ("average_volume",),
        "change_pct": ("change",),
        "eps_ttm": ("eps",),
        "eps_next_y_growth": ("eps_next_y",),
        "eps_past_3_5y": ("eps_past_5y",),
        "income": ("net_income",),
        "index": ("index_membership",),
        "ipo": ("ipo_date",),
        "oper_margin": ("operating_margin",),
        "option_short": ("optionable", "shortable"),
        "perf_week": ("perf_1w",),
        "perf_month": ("perf_4w",),
        "perf_quarter": ("perf_13w",),
        "perf_half_y": ("perf_26w",),
        "perf_year": ("perf_52w",),
        "profit_margin": ("net_margin",),
        "rel_volume": ("relative_volume",),
        "rsi_14": ("rsi",),
        "sales_past_3_5y": ("sales_past_5y",),
        # Finviz prints this one cell with two windows in it. A one-cell tab can
        # only draw one, and the week window is what the frozen tab has always
        # drawn; ``volatility_4w`` stays addressable by its own name.
        "volatility": ("volatility_1w",),
        "week52_high": ("high_52w",),
        "week52_low": ("low_52w",),
    }
    assert len(V.LEGACY_COLUMN_ALIASES) == 23
    # The one name that really is two cells keeps both parts, because a client
    # that names it has to show two columns.
    composites = {name: keys for name, keys in V.LEGACY_COLUMN_ALIASES.items() if len(keys) > 1}
    assert composites == {"option_short": ("optionable", "shortable")}
    # ... and every name is also a row field the renderer can read, except that
    # composite, which is a documented absence.
    from app.screen import columns as column_map

    for name in V.LEGACY_COLUMN_ALIASES:
        resolvable = column_map.field_for(name) is not None
        documented = column_map.absent_reason(name) is not None
        assert resolvable or documented, f"{name!r} is neither mapped nor documented"
        if name != "option_short":
            assert resolvable, f"{name!r} should render a cell"


def test_the_52_week_aliases_keep_our_price_meaning() -> None:
    """Finviz prints a distance from the high; our store holds the price.

    ``depth_fields`` overrides the catalogue's ``pct`` unit to ``USD`` for these
    four cells, and the shipped client turns them into a range position
    (``(price - low) / (high - low)``), which only works on prices. The alias
    therefore carries a documented unit shift instead of silently changing what
    the number means.
    """

    from app.market.depth_fields import FIELD_SPECS

    assert FIELD_SPECS["high_52w"].unit == "USD"
    assert FIELD_SPECS["low_52w"].unit == "USD"
    assert set(V.LEGACY_UNIT_SHIFT) == {"week52_high", "week52_low"}
    for name, note in V.LEGACY_UNIT_SHIFT.items():
        assert "%" in note and "price" in note.lower(), (
            f"{name} must say, in words, that the alias swaps a %-distance for a price"
        )


def test_the_vanilla_client_speaks_only_names_that_resolve() -> None:
    """No phantom names in the client, proven rather than promised."""

    app_js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    meta_block = app_js.split("const COLUMN_META = {", 1)[1].split("\n};", 1)[0]
    meta = set(re.findall(r"^ {2}([a-z0-9_]+):", meta_block, re.MULTILINE))
    views_block = app_js.split("const SCREEN_VIEWS = {", 1)[1].split("SCREEN_VIEWS.all", 1)[0]
    client_columns = set()
    for line in views_block.splitlines():
        match = re.search(r"columns:\s*\[([^\]]*)\]", line)
        if match:
            client_columns |= set(re.findall(r'"([a-z0-9_]+)"', match.group(1)))

    assert len(meta) >= 60, f"the COLUMN_META parser stopped matching: found {len(meta)} names"
    assert client_columns, "the SCREEN_VIEWS parser stopped matching"

    # One COLUMN_META entry is not a view column at all: the client merges the
    # browse row (whose market cap is spelled with its unit) into the selection
    # row, and that name is filled by the row, not by a view.
    row_fields = {"market_cap_usd"}
    phantom = sorted(
        name
        for name in meta | client_columns
        if name not in CATALOG_KEYS
        and name not in V.LEGACY_COLUMN_ALIASES
        and name not in row_fields
    )
    assert phantom == [], f"the client renders names with no catalogue home: {phantom}"

    tabs = set(
        re.findall(
            r'data-screen-view="([a-z0-9_]+)"',
            (ROOT / "ui" / "index.html").read_text(encoding="utf-8"),
        )
    )
    assert tabs, "the tab parser stopped matching"
    assert tabs <= set(V.SCREEN_VIEW_NAMES), (
        f"tabs the server would reject: {sorted(tabs - set(V.SCREEN_VIEW_NAMES))}"
    )


# ---------------------------------------------------------------------------
# Rendering: one cell per declared column, null with a reason
# ---------------------------------------------------------------------------


def test_building_a_view_keeps_the_declared_column_order() -> None:
    row = V.build_view("value", SNAPSHOT)
    assert row.keys == V.columns("value")
    assert tuple(row.as_dict()) == V.columns("value")


def test_a_complete_row_fills_every_view() -> None:
    """The *renderer the API uses* fills every view from a row that has the data.

    This test used to feed ``build_view`` — which takes catalogue-keyed cells
    directly — a dict of ``{key: 1.0 for key in CATALOG_KEYS}`` and never touched
    ``app.screen.selection.view_row``, the function ``POST /screens/select``
    actually renders with. It therefore passed on a tree where nine views
    returned null for 62 of their 100 columns, because it never asked the renderer
    that reads row fields. It asks it now.
    """

    from app.screen import columns as column_map
    from app.screen.selection import view_row

    # A row carrying every column a row can carry, populated. This is the
    # structural upper bound: if the resolver names a field, the cell exists.
    row: dict[str, Any] = {name: 1.0 for name in column_map.ROW_FIELD_COLUMNS}
    row.update({"symbol": "SYN", "rank": 1, "name": "Synthetic Holdings"})

    absent = set(column_map.ABSENT_COLUMNS)
    holes: list[tuple[str, str]] = []
    for view in V.VIEWS:
        rendered = view_row(row, view.name, as_of="2026-10-30T00:00:00Z")
        assert list(rendered)[4:] == list(view.columns), (
            f"{view.name} must render its declared columns in order"
        )
        for column in view.columns:
            if rendered[column] is None and column not in absent:
                holes.append((view.name, column))
    assert holes == [], (
        "a fully populated row still renders these view columns null, so the "
        f"resolver has no field for them: {holes}"
    )
    # The only holes are the documented session cells — and there are three.
    assert sorted(
        (view.name, column) for view in V.VIEWS for column in view.columns
        if view_row(row, view.name, as_of="x")[column] is None
    ) == [("today", "high"), ("today", "low"), ("today", "open")]


def test_a_missing_row_is_all_null_with_reasons() -> None:
    row = V.build_view("value", {})
    assert row.total == len(V.columns("value"))
    assert set(row.values.values()) == {None}, "a missing cell is null, never 0"
    assert row.missing_keys == V.columns("value")
    assert {reason for _key, reason in row.missing} == {F.MissingReason.ABSENT}
    assert row.fill_rate == 0.0


def test_a_partial_cell_keeps_its_value_and_is_not_counted_as_filled() -> None:
    # Built by hand: the partial marker is the parser's contract, and the point
    # here is that the renderer honours it for a catalogue key, not that the
    # Finviz quote parser produces it for one particular metric.
    cells = {
        "high_52w": F.ParsedCell(
            key="high_52w", value=348.36, reason=F.MissingReason.PARTIAL, raw="348.36 N/A"
        )
    }
    row = V.build_view("trend", cells)

    assert dict(row.cells)["high_52w"] == pytest.approx(348.36)
    assert dict(row.missing)["high_52w"] is F.MissingReason.PARTIAL
    assert row.filled == 0, "a half cell is visible data, but it is not a filled column"
    assert row.total == len(V.columns("trend"))


def test_an_unparsed_cell_keeps_its_reason() -> None:
    cells = {"pe": F.parse_cell("pe", "abc")}
    row = V.build_view("value", cells)
    assert row.values["pe"] is None
    assert dict(row.missing)["pe"] is F.MissingReason.UNPARSED


def test_a_real_zero_counts_as_filled() -> None:
    cells = F.parse_snapshot([{"name": "Current Ratio", "raw": "0.00"}])
    row = V.build_view("quality", cells)

    assert row.values["current_ratio"] == pytest.approx(0.0)
    assert "current_ratio" not in row.missing_keys, "0 is a value; only None is a hole"
    assert row.filled == 1


def test_plain_values_are_accepted_for_a_canonical_row() -> None:
    row = V.build_view("today", {"price": 347.79, "volume": None})
    assert row.values["price"] == pytest.approx(347.79)
    assert row.values["volume"] is None
    assert dict(row.missing)["volume"] is F.MissingReason.ABSENT
    assert row.filled == 1


def test_fill_rate_is_filled_over_declared() -> None:
    row = V.build_view("income", {"dividend": 0.24, "dividend_yield": 0.51})
    assert row.total == 8
    assert row.filled == 2
    assert row.fill_rate == pytest.approx(2 / 8)
    assert V.fill_rate("income", {"dividend": 0.24, "dividend_yield": 0.51}) == pytest.approx(
        2 / 8
    )


def test_a_row_keeps_its_values_in_view_order() -> None:
    payload = V.build_view("profile", SNAPSHOT).as_dict()
    assert list(payload) == list(V.columns("profile"))
    assert payload["index_membership"] in (None, ("DJIA", "NDX", "S&P 500")), (
        "a multi-part cell keeps its parts; the API layer decides how to render them"
    )
