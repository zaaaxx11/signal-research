"""The nine screener views, written in the 116-column catalogue's own keys.

Why this file was rewritten
---------------------------
The Finviz-era table named 71 columns by hand. Twenty-three of those names —
``eps_ttm``, ``perf_week``, ``atr_14``, ``week52_high``, ``option_short`` … —
were never catalogue keys, so any renderer built on :data:`VIEWS` could show at
most 48 of the 116 columns the engine fills (41 %), with 23 blanks that no
amount of data ever fills. The operator's design (plan §6.1, D12) replaces the
six Finviz tabs with nine views derived from the catalogue's own categories, each
answering one question, and **every** column named here is a key declared by
``app/engine/data/finviz_columns_116.json``.

The invariant, enforced at import and again in CI
-------------------------------------------------
The nine views plus :data:`DETAIL_ONLY` partition the catalogue: 116 keys, each
with exactly one home, zero unknown names, zero orphans. ``all`` is not a
hand-written list either — it is the catalogue in catalogue order, so it cannot
drift from the source of truth by construction.

Three deliberate shapes in this module:

* **A view is data, not a code path.** One declarative table, no per-view branch:
  adding a column is a one-line diff and a wrong key fails the import.
* **Detail-only is a decision, not an oversight.** Sixteen keys (prose, URLs,
  transport plumbing, the identity lane) are reachable in the detail panel and
  are never columns, so no view is a 116-column spreadsheet.
* **Legacy names are aliases, not phantoms.** The shipped vanilla client still
  speaks ``valuation``/``technical``/``perf_week``; those tabs keep their frozen
  column sets and every one of their 23 non-catalogue names is recorded in
  :data:`LEGACY_COLUMN_ALIASES` with the catalogue key that answers it. That
  block is the migration list: when the old client retires, delete
  :data:`LEGACY_VIEWS` together with the three alias tables above it, and nothing
  outside the compatibility section changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from app.market.depth_fields import field_names
from app.market.fields import CellValue, MissingReason, ParsedCell
from app.screen import columns as column_map

#: The 116 catalogue keys, in the order the catalogue declares them. Read from
#: the one registry that already parses ``finviz_columns_116.json`` instead of
#: copying the names here: a hand-kept list is exactly how the old table drifted.
CATALOG_KEYS: tuple[str, ...] = field_names()
CATALOG_KEY_SET: frozenset[str] = frozenset(CATALOG_KEYS)


@dataclass(frozen=True, slots=True)
class ViewDefinition:
    """One view: what it is called, the question it answers, its columns.

    ``job`` is the one-line answer a client shows next to the tab label, so the
    view table doubles as the UI's metadata instead of being re-described in
    JavaScript (where the two lists drifted apart before).
    """

    name: str
    label: str
    job: str
    columns: tuple[str, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if len(set(self.columns)) != len(self.columns):
            raise RuntimeError(f"view {self.name!r} repeats a column")


@dataclass(frozen=True, slots=True)
class ViewRow:
    """One symbol rendered in one view, columns in view order.

    ``cells`` keeps one entry per declared column — the row is never filtered
    down to the columns that happen to have data, because a table whose shape
    depends on the payload cannot be compared between two runs.
    """

    view: str
    label: str
    cells: tuple[tuple[str, CellValue], ...]
    missing: tuple[tuple[str, MissingReason], ...]
    filled: int

    @property
    def total(self) -> int:
        return len(self.cells)

    @property
    def fill_rate(self) -> float:
        """Filled columns / declared columns (0.0 for a view with no columns)."""

        return self.filled / self.total if self.total else 0.0

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(key for key, _value in self.cells)

    @property
    def values(self) -> dict[str, CellValue]:
        return dict(self.cells)

    @property
    def missing_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _reason in self.missing)

    def as_dict(self) -> dict[str, CellValue]:
        """Cells in view order; ``None`` stays ``None``.

        Values keep their parsed shape (a ``date``, a tuple of parts); rendering
        them for a client is the API layer's job, not the view's.
        """

        return dict(self.cells)


#: The nine views, in the order the plan lists them (plan §6.1). Order inside a
#: view is reading order for the job, not the catalogue's declaration order: the
#: identity of the number (price, change) comes before the ratio derived from it.
VIEW_NAMES: tuple[str, ...] = (
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

ALL_VIEW_NAME = "all"

VIEWS: tuple[ViewDefinition, ...] = (
    ViewDefinition(
        name="today",
        label="Today",
        job="What is moving right now?",
        columns=(
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
        description=(
            "The session, in the order a trader reads it: the price, the three "
            "ways it moved (from yesterday's close, from today's open, the open "
            "gap), then the bar itself, then the three volumes that say whether "
            "the move is real."
        ),
    ),
    ViewDefinition(
        name="value",
        label="Value",
        job="Is it cheap?",
        columns=(
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
        description=(
            "Size first, then every multiple the catalogue carries, then the two "
            "analyst cells that say what the street thinks of the price. All "
            "ratios except the two currency amounts, so the view reads without a "
            "unit column."
        ),
    ),
    ViewDefinition(
        name="quality",
        label="Quality",
        job="Is the business good?",
        columns=(
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
        description=(
            "Returns on capital, then the balance sheet that produces them, then "
            "the three margins down the income statement. Ratios only: this is "
            "the 'can it earn' view, not the 'what does it earn' view."
        ),
    ),
    ViewDefinition(
        name="growth",
        label="Growth",
        job="Is it growing?",
        columns=(
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
        description=(
            "The two absolute levels a growth rate is computed from, then the "
            "forward estimates, then the realised history newest-first: quarter "
            "over quarter, trailing year, and the multi-year trend. Every cell is "
            "an EPS or a revenue series, so the columns compare."
        ),
    ),
    ViewDefinition(
        name="momentum",
        label="Momentum",
        job="How has it performed?",
        columns=(
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
        description=(
            "Returns from one week to ten years, shortest window first so the row "
            "reads as a horizon, then the one indicator that says whether the "
            "move is stretched."
        ),
    ),
    ViewDefinition(
        name="trend",
        label="Trend",
        job="Where is it relative to its own history?",
        columns=(
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
        description=(
            "The three moving averages as distances, then the four range anchors, "
            "then the three risk cells. SMA and range cells are percent distances "
            "from the level (not the level itself), which the catalogue states and "
            "the UI must label; ``high_52w``/``low_52w`` are prices."
        ),
    ),
    ViewDefinition(
        name="ownership",
        label="Ownership",
        job="Who owns it, and who is short?",
        columns=(
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
        description=(
            "The two share counts everything else is a percentage of, then who "
            "holds them and whether they are buying, then the three short-side "
            "cells. ``option_short`` from the old table is not one cell here: "
            "``optionable`` and ``shortable`` are two catalogue keys with two "
            "different sources and live in the profile view."
        ),
    ),
    ViewDefinition(
        name="income",
        label="Income",
        job="What does it pay?",
        columns=(
            "dividend",
            "dividend_yield",
            "dividend_ttm",
            "payout_ratio",
            "dividend_ex_date",
            "dividend_growth_1y",
            "dividend_growth_3y",
            "dividend_growth_5y",
        ),
        description=(
            "The payment, its yield and the cash actually paid over the year, "
            "then the share of earnings it costs, the date it goes ex, and the "
            "growth of the payment itself. Eight cells: dividends are one "
            "question, not a tab hidden inside a 37-column financial view."
        ),
    ),
    ViewDefinition(
        name="profile",
        label="Profile",
        job="What is it?",
        columns=(
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
        description=(
            "Identity, then classification, then the two tradability flags and "
            "the two dates that age the company. This is where "
            "``index_membership`` lives: the old overview was its only home, which "
            "is why the overview could not simply be deleted."
        ),
    ),
)

#: Views a client may ask for by their canonical name.
CANONICAL_VIEW_NAMES: tuple[str, ...] = VIEW_NAMES + (ALL_VIEW_NAME,)

#: The 116 keys that are deliberately never a column: prose, URLs, transport
#: plumbing and the identity lane the table pins instead of scrolling to. They
#: are part of the partition (that is what keeps "116/116 exactly once" honest),
#: and each group says why it is not a cell you scan at 100 rows.
DETAIL_ONLY: tuple[str, ...] = (
    # Long-run estimates and surprise deltas read one symbol at a time; a column
    # of them at 100 rows is noise, and two of them (the "-5y" series) are empty
    # by provider fact rather than by omission.
    "eps_next_5y",
    "sales_past_3y",
    "sales_past_5y",
    "eps_surprise",
    "revenue_surprise",
    # A band, a classifier and an outlier: the detail panel can afford to
    # explain them, a 12-column row cannot.
    "all_time_low",
    "candlestick_pattern",
    "week_52_range",
    # News is prose and links; it belongs beside the chart, not in a numeric row.
    "news_url",
    "news_title",
    "news_time",
    "daily_digest",
    # The company's own URL is a link target, not a scannable cell.
    "website",
    # The identity lane is pinned, not a column the client picks.
    "ticker",
    # Transport plumbing the screener uses to order and type rows; never rendered.
    "sort_order",
    "row_type",
)

#: The full table Finviz never offers as one tab, and the plan keeps as an
#: explicit destination rather than a default. Built from the catalogue, so it is
#: 116 keys in catalogue order by construction — no second list to drift.
ALL_COLUMNS: tuple[str, ...] = CATALOG_KEYS

ALL_VIEW = ViewDefinition(
    name=ALL_VIEW_NAME,
    label="All columns",
    job="What does the whole catalogue look like at once?",
    columns=ALL_COLUMNS,
    description=(
        "Every renderable catalogue key in catalogue order. It is a destination, "
        "not a default: 116 columns is a spreadsheet, and a cell the engine does "
        "not carry stays null instead of being invented to fill the width."
    ),
)


# ---------------------------------------------------------------------------
# Compatibility: the shipped vanilla client, by alias rather than by phantom
# ---------------------------------------------------------------------------

#: The two aliases whose *unit* changes with the rename. Finviz prints these
#: cells as a percent distance from the 52-week extreme; our catalogue stores the
#: price (`depth_fields` overrides the unit to USD), and the shipped client turns
#: them into a range position, ``(price - low) / (high - low)``. The alias keeps
#: what our store holds, so any consumer that multiplies one by the price would
#: be inventing a percentage — say so instead of changing the number quietly.
LEGACY_UNIT_SHIFT: Mapping[str, str] = MappingProxyType(
    {
        "week52_high": (
            "Source panel: % distance from the 52-week high. Ours: the high as a price "
            "in USD (depth_fields unit override); do not re-derive a percentage."
        ),
        "week52_low": (
            "Source panel: % distance from the 52-week low. Ours: the low as a price in "
            "USD (depth_fields unit override); do not re-derive a percentage."
        ),
    }
)

#: Where an old tab's subject lives now. ``financial`` was split three ways and
#: is recorded against the view that inherited its income-statement cells.
#: ``ownership`` is absent because it kept its *name* — but not its column list:
#: the Finviz tab was seven columns led by insider ownership and one collapsed
#: ``option_short`` cell, while the canonical view leads with the share counts and
#: carries the three short-side cells. The shipped client therefore gets the
#: canonical row for that tab and reads six of its seven names from it.
LEGACY_VIEW_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "overview": "today",
        "valuation": "value",
        "performance": "momentum",
        "financial": "growth",
        "technical": "trend",
    }
)

#: The shipped client's renamed tabs, frozen. They are **not** views: they are
#: the compatibility surface a client written against the Finviz vocabulary reads,
#: so their column tuples must stay byte-identical to what ``ui/app.js`` renders.
#: Nothing new may be added here; when the vanilla client retires, this table and
#: the three compatibility constants above it are deleted together.
LEGACY_VIEWS: tuple[ViewDefinition, ...] = (
    ViewDefinition(
        name="overview",
        label="Overview",
        job="Frozen: what the shipped client's Overview tab renders.",
        columns=(
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
        description="Superseded by today; kept so an open browser keeps its columns.",
    ),
    ViewDefinition(
        name="valuation",
        label="Valuation",
        job="Frozen: what the shipped client's Valuation tab renders.",
        columns=(
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
        ),
        description="Superseded by value; must stay column-identical to ui/app.js.",
    ),
    ViewDefinition(
        name="performance",
        label="Performance",
        job="Frozen: what the shipped client's Performance tab renders.",
        columns=(
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
        description="Superseded by momentum; must stay column-identical to ui/app.js.",
    ),
    ViewDefinition(
        name="financial",
        label="Financial",
        job="Frozen: what the shipped client's Financial tab renders.",
        columns=(
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
        description="Split into quality, growth and income; the old tab stays for one release.",
    ),
    ViewDefinition(
        name="technical",
        label="Technical",
        job="Frozen: what the shipped client's Technical tab renders.",
        columns=(
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
        description="Superseded by trend (and today, for the session cells).",
    ),
)


#: The 23 names the Finviz-era table used that the catalogue never declared, and
#: the catalogue key(s) that answer each one (plan §6.2, measured in the audit).
#:
#: **Derived, not written here.** The meaning of every name — current, legacy or
#: composite — is stated once in :data:`app.screen.columns.CATALOGUE_COLUMNS`;
#: this table is the inverse of that one (old name → the catalogue keys sharing its
#: row field), computed by :func:`app.screen.columns.catalogue_keys_for`. The key
#: list comes from the frozen tabs below, so an alias cannot exist that no client
#: renders. A tuple because two cells really are two cells: ``option_short`` is
#: ``optionable`` *and* ``shortable`` (different providers); ``volatility`` is the
#: week window the frozen tab has always drawn, with ``volatility_4w`` addressable
#: by its own name.
def _legacy_column_names() -> tuple[str, ...]:
    """The non-catalogue column names the frozen tabs render, in tab order."""

    return tuple(
        dict.fromkeys(
            column
            for view in LEGACY_VIEWS
            for column in view.columns
            if column not in CATALOG_KEY_SET
        )
    )


LEGACY_COLUMN_ALIASES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {name: column_map.catalogue_keys_for(name) for name in _legacy_column_names()}
)


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

#: Every request name a client may send, canonical first. The deprecated names
#: stay because a browser with ``ui/app.js`` open keeps sending them; the API
#: validates against this tuple, so they must resolve to a real definition.
SCREEN_VIEW_NAMES: tuple[str, ...] = CANONICAL_VIEW_NAMES + tuple(
    view.name for view in LEGACY_VIEWS
)

VIEW_BY_NAME: dict[str, ViewDefinition] = {
    **{view.name: view for view in VIEWS},
    ALL_VIEW_NAME: ALL_VIEW,
    **{view.name: view for view in LEGACY_VIEWS},
}

#: Request name → ordered columns, the shape ``app.screen.selection`` projects a
#: row with. It carries the deprecated tabs too, so their responses keep their
#: exact column set; :func:`view_catalogue` is the canonical metadata a new client
#: should fetch instead.
VIEW_COLUMNS: dict[str, tuple[str, ...]] = {
    name: definition.columns for name, definition in VIEW_BY_NAME.items()
}


def _validate() -> None:
    """Fail at import: a view that lost a key must not boot a service."""

    names = tuple(view.name for view in VIEWS)
    if names != VIEW_NAMES:
        raise RuntimeError(f"views must be {VIEW_NAMES}, got {names}")
    if len({view.label for view in VIEWS}) != len(VIEWS):
        raise RuntimeError("view labels must be unique")

    owner: dict[str, str] = {}
    unknown: dict[str, list[str]] = {}
    for view in VIEWS:
        if len(view.columns) < 5:
            raise RuntimeError(f"view {view.name!r} is too small to be a view")
        for key in view.columns:
            if key not in CATALOG_KEY_SET:
                unknown.setdefault(key, []).append(view.name)
                continue
            if key in owner:
                raise RuntimeError(
                    f"{key!r} is in both {owner[key]!r} and {view.name!r}; a catalogue "
                    "key has exactly one home"
                )
            owner[key] = view.name
    if unknown:
        raise RuntimeError(
            f"{len(unknown)} view column(s) are not catalogue keys: "
            + ", ".join(f"{key} in {'/'.join(carried)}" for key, carried in sorted(unknown.items()))
        )

    assigned = set(owner) | set(DETAIL_ONLY)
    orphans = [key for key in CATALOG_KEYS if key not in assigned]
    if orphans:
        raise RuntimeError(f"{len(orphans)} catalogue key(s) have no home: {orphans}")
    foreign = sorted(assigned - CATALOG_KEY_SET)
    if foreign:
        raise RuntimeError(f"assigned names that are not catalogue keys: {foreign}")
    both = sorted(set(owner) & set(DETAIL_ONLY))
    if both:
        raise RuntimeError(f"keys that are both a view column and detail-only: {both}")

    canonical = set(CANONICAL_VIEW_NAMES)
    clashes = sorted(canonical & {view.name for view in LEGACY_VIEWS})
    if clashes:
        raise RuntimeError(f"legacy views shadow a canonical name: {clashes}")

    used: set[str] = set()
    for view in LEGACY_VIEWS:
        for column in view.columns:
            used.add(column)
            if column in CATALOG_KEY_SET:
                continue
            if column not in LEGACY_COLUMN_ALIASES:
                raise RuntimeError(
                    f"legacy view {view.name!r} names {column!r}, which is neither a "
                    "catalogue key nor a recorded alias; that is a phantom column"
                )
    dead = sorted(set(LEGACY_COLUMN_ALIASES) - used)
    if dead:
        raise RuntimeError(f"alias entries no legacy view uses: {dead}")
    bad_targets = sorted(
        {target for targets in LEGACY_COLUMN_ALIASES.values() for target in targets}
        - CATALOG_KEY_SET
    )
    if bad_targets:
        raise RuntimeError(f"alias targets that are not catalogue keys: {bad_targets}")

    # The check the audit found missing: every column a canonical view declares
    # must resolve to a row field, or be one of the documented absences. A view
    # column with no entry is how 62 of the 100 canonical columns rendered null
    # while the data sat on the row.
    declared = set().union(*(set(view.columns) for view in VIEWS))
    unmapped = sorted(
        declared - set(column_map.CATALOGUE_COLUMNS) - set(column_map.ABSENT_COLUMNS)
    )
    if unmapped:
        raise RuntimeError(
            f"{len(unmapped)} canonical view column(s) resolve to no row field: {unmapped}"
        )
    # Stated as a number rather than a comment: three session cells are the only
    # canonical view columns the row layer cannot answer, and a fourth would be
    # the same bug growing back.
    absent = sorted(declared & set(column_map.ABSENT_COLUMNS))
    if absent != ["high", "low", "open"]:
        raise RuntimeError(
            "the only view columns without a row field must be the session "
            f"open/high/low, got {absent}"
        )
    dead_legacy = sorted(
        name
        for name in _legacy_column_names()
        if column_map.field_for(name) is None
        and column_map.absent_reason(name) is None
    )
    if dead_legacy:
        raise RuntimeError(
            f"legacy column name(s) with no meaning in the resolver: {dead_legacy}"
        )


_validate()


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def view(name: str) -> ViewDefinition:
    """The view named ``name``; an unknown name is a caller error."""

    try:
        return VIEW_BY_NAME[name]
    except KeyError:
        raise ValueError(
            f"unknown view {name!r}; expected one of {', '.join(SCREEN_VIEW_NAMES)}"
        ) from None


def columns(name: str) -> tuple[str, ...]:
    """The ordered column keys of ``name``."""

    return view(name).columns


def is_catalogue_key(name: str) -> bool:
    return name in CATALOG_KEY_SET


def resolve_column(name: str) -> tuple[str, ...]:
    """The catalogue key(s) a client name means; ``()`` when it means nothing.

    A current key resolves to itself, a Finviz-era name to its successor(s), and
    a documented composite to its parts. The answer is the inverse of
    :data:`app.screen.columns.CATALOGUE_COLUMNS`, so it cannot drift from the
    renderer's own mapping. The UI uses this to map a stored column preference
    after the rename; returning ``()`` rather than raising keeps it a lookup, not
    a validator.
    """

    return column_map.catalogue_keys_for(name)


def successor_view(name: str) -> str:
    """The canonical view that inherited ``name`` (itself when it did not move)."""

    return LEGACY_VIEW_ALIASES.get(name, name)


def view_catalogue() -> list[dict[str, Any]]:
    """JSON-ready metadata for every canonical view, ``all`` included.

    A client renders its tabs and column pickers from this and never hardcodes a
    column name again — which is what let the JavaScript table and the server
    table drift in the first place. Returned fresh on each call so a caller
    cannot mutate the module's table through the payload.
    """

    return [
        {
            "name": definition.name,
            "label": definition.label,
            "job": definition.job,
            "columns": list(definition.columns),
        }
        for definition in (*VIEWS, ALL_VIEW)
    ]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def build_view(name: str, cells: Mapping[str, ParsedCell | CellValue]) -> ViewRow:
    """Render ``cells`` as view ``name``, keeping every declared column.

    ``cells`` is normally the ``{catalogue key: ParsedCell}`` a
    :func:`app.market.fields.parse_snapshot` produced, but a plain
    ``{key: value}`` mapping is accepted so a canonical row can be displayed
    without inventing a parse step. A key that is absent — or present with
    ``None`` — becomes ``None`` with reason ``absent``; a cell whose parser
    recorded ``unparsed`` or ``partial`` keeps that reason, so the UI can say
    *why* a number is missing instead of printing a zero.
    """

    definition = view(name)
    ordered: list[tuple[str, CellValue]] = []
    missing: list[tuple[str, MissingReason]] = []
    filled = 0

    for key in definition.columns:
        cell = cells.get(key)
        if isinstance(cell, ParsedCell):
            value: CellValue = cell.value
            reason = cell.reason
        else:
            value = cell
            reason = None if cell is not None else MissingReason.ABSENT

        ordered.append((key, value))
        if value is None or reason is not None:
            missing.append((key, reason or MissingReason.ABSENT))
        else:
            filled += 1

    return ViewRow(
        view=definition.name,
        label=definition.label,
        cells=tuple(ordered),
        missing=tuple(missing),
        filled=filled,
    )


def fill_rate(name: str, cells: Mapping[str, ParsedCell | CellValue]) -> float:
    """Filled columns / declared columns for one view (the P1 gate metric)."""

    return build_view(name, cells).fill_rate


__all__ = [
    "ALL_COLUMNS",
    "ALL_VIEW",
    "ALL_VIEW_NAME",
    "CANONICAL_VIEW_NAMES",
    "CATALOG_KEYS",
    "CATALOG_KEY_SET",
    "DETAIL_ONLY",
    "LEGACY_COLUMN_ALIASES",
    "LEGACY_UNIT_SHIFT",
    "LEGACY_VIEWS",
    "LEGACY_VIEW_ALIASES",
    "SCREEN_VIEW_NAMES",
    "VIEWS",
    "VIEW_BY_NAME",
    "VIEW_COLUMNS",
    "VIEW_NAMES",
    "ViewDefinition",
    "ViewRow",
    "build_view",
    "columns",
    "fill_rate",
    "is_catalogue_key",
    "resolve_column",
    "successor_view",
    "view",
    "view_catalogue",
]
