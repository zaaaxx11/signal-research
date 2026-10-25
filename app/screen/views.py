"""Six views over the 71 Finviz quote metrics, with hand-written column lists.

Finviz's quote page has six tabs — Overview, Valuation, Ownership, Performance,
Financial, Technical — and PLAN-finviz-parity.md §2 puts "6 view" in the field
contract next to the 71 metrics. A view here is deliberately **not** a predicate
over :data:`app.market.fields.METRICS`: it is an ordered tuple of column keys
written out by hand, so removing or reordering a column is a one-line diff
instead of a side effect of some filter somebody edited elsewhere.

Three layout rules, each visible in a test:

* the five specialist views (valuation, ownership, performance, financial,
  technical) **partition** the 71 metrics — every metric has exactly one
  specialist home, so nothing is reachable only by scrolling a 66-column dump;
* ``overview`` is the at-a-glance cross-section and touches every specialist
  view at least once, in reading order rather than Finviz's DOM order;
* ``index`` (DJIA/NDX/S&P 500 membership) has no specialist home — it is a
  classification cell, not a measurement — so it is declared in
  :data:`OVERVIEW_ONLY` instead of being quietly dropped.

:func:`build_view` never shortens a row: a column with no value keeps its place
with ``None`` plus the :class:`~app.market.fields.MissingReason` the parser
recorded (``absent``, ``unparsed`` or ``partial``). ``fill_rate`` is the P1 gate
metric — "field fill-rate per view ≥ Finviz", measured per view, not per row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.market.fields import (
    METRIC_BY_KEY,
    CellValue,
    MissingReason,
    ParsedCell,
)

#: The six tabs, in the order Finviz shows them.
VIEW_NAMES: tuple[str, ...] = (
    "overview",
    "valuation",
    "ownership",
    "performance",
    "financial",
    "technical",
)

#: Metrics that only exist in the overview: a classification, not a measurement.
OVERVIEW_ONLY: tuple[str, ...] = ("index",)


@dataclass(frozen=True, slots=True)
class ViewDefinition:
    """One tab: what it is called and exactly which columns it shows."""

    name: str
    title: str
    columns: tuple[str, ...]
    description: str

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
    title: str
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


VIEWS: tuple[ViewDefinition, ...] = (
    ViewDefinition(
        name="overview",
        title="Overview",
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
        description=(
            "The glance: price and change first, then size and valuation, then "
            "the two 52-week anchors, then ownership and the profile cells. "
            "One column from every specialist view, not the whole page."
        ),
    ),
    ViewDefinition(
        name="valuation",
        title="Valuation",
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
        description=(
            "Size and multiples. Every cell is a ratio except the two currency "
            "amounts, so a view can be read without a unit column."
        ),
    ),
    ViewDefinition(
        name="ownership",
        title="Ownership",
        columns=(
            "insider_own",
            "insider_trans",
            "inst_own",
            "inst_trans",
            "shares_outstanding",
            "shares_float",
            "option_short",
        ),
        description=(
            "Who holds the shares and whether they are buying: insider and "
            "institutional ownership plus the two share counts they are a "
            "percentage of."
        ),
    ),
    ViewDefinition(
        name="performance",
        title="Performance",
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
        description=(
            "Returns over the windows the canonical table models (ret_1w … "
            "ret_5y) plus the 52-week anchors, which are printed as a price and "
            "a distance."
        ),
    ),
    ViewDefinition(
        name="financial",
        title="Financial",
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
        description=(
            "The statement half: what the company earns and sells, the per-share "
            "book and cash, the growth rates Finviz prints beside them, margins, "
            "liquidity, leverage and the two profile cells that date the company."
        ),
    ),
    ViewDefinition(
        name="technical",
        title="Technical",
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
        description=(
            "Price, session volume and the trading indicators. SMA20/50/200 are "
            "distances in percent, not averages — the view keeps that unit "
            "rather than pretending the cell is a moving average."
        ),
    ),
)

#: The full table Finviz never offers as one tab: every renderable metric, in
#: reading order (overview first, then each specialist view), with duplicates
#: removed. It is a *view* for the API and UI, but not a seventh tab in the
#: TabDefinition partition above; the six-tab contract stays intact.
ALL_VIEW_NAME = "all"
ALL_COLUMNS: tuple[str, ...] = tuple(
    dict.fromkeys(key for view in VIEWS for key in view.columns)
)
ALL_VIEW = ViewDefinition(
    name=ALL_VIEW_NAME,
    title="All columns",
    columns=ALL_COLUMNS,
    description=(
        "Every renderable Finviz metric in one horizontally scrollable table. "
        "A cell the engine does not carry stays null; the table never invents a "
        "number to fill the width."
    ),
)

VIEW_BY_NAME: dict[str, ViewDefinition] = {
    **{view.name: view for view in VIEWS},
    ALL_VIEW_NAME: ALL_VIEW,
}
VIEW_COLUMNS: dict[str, tuple[str, ...]] = {
    **{view.name: view.columns for view in VIEWS},
    ALL_VIEW_NAME: ALL_COLUMNS,
}
#: Views a selection may ask for: the six Finviz tabs plus the full table.
SCREEN_VIEW_NAMES: tuple[str, ...] = VIEW_NAMES + (ALL_VIEW_NAME,)

#: The five tabs that between them own every metric except :data:`OVERVIEW_ONLY`.
SPECIALIST_VIEW_NAMES: tuple[str, ...] = VIEW_NAMES[1:]


def _validate(views: tuple[ViewDefinition, ...]) -> None:
    """Fail at import: a view that lost a column must not boot a service."""

    names = [view.name for view in views]
    if tuple(names) != VIEW_NAMES:
        raise RuntimeError(f"views must be {VIEW_NAMES}, got {tuple(names)}")
    if len({view.title for view in views}) != len(views):
        raise RuntimeError("view titles must be unique")

    for view in views:
        if len(view.columns) < 5:
            raise RuntimeError(f"view {view.name!r} is too small to be a view")
        unknown = [key for key in view.columns if key not in METRIC_BY_KEY]
        if unknown:
            raise RuntimeError(
                f"view {view.name!r} names unknown metric(s) {unknown}; "
                "the quote table has 71"
            )

    specialist: list[str] = []
    for name in SPECIALIST_VIEW_NAMES:
        specialist.extend(VIEW_COLUMNS[name])
    if len(set(specialist)) != len(specialist):
        raise RuntimeError(
            "the specialist views must partition the metrics; a column appears twice"
        )
    expected = set(METRIC_BY_KEY) - set(OVERVIEW_ONLY)
    if set(specialist) != expected:
        missing = sorted(expected - set(specialist))
        extra = sorted(set(specialist) - expected)
        raise RuntimeError(
            f"specialist views must cover every metric except {OVERVIEW_ONLY}: "
            f"missing={missing}, extra={extra}"
        )

    overview = set(VIEW_COLUMNS["overview"])
    if not overview <= set(METRIC_BY_KEY):
        raise RuntimeError("overview names a metric that does not exist")
    for name in SPECIALIST_VIEW_NAMES:
        if not overview & set(VIEW_COLUMNS[name]):
            raise RuntimeError(
                f"overview does not touch {name!r}; an at-a-glance tab that "
                "ignores a whole dimension is a header, not a view"
            )
    if set(OVERVIEW_ONLY) - overview:
        raise RuntimeError(f"{OVERVIEW_ONLY} must live in the overview")


_validate(VIEWS)


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


def build_view(name: str, cells: Mapping[str, ParsedCell | CellValue]) -> ViewRow:
    """Render ``cells`` as view ``name``, keeping every declared column.

    ``cells`` is normally the ``{metric key: ParsedCell}`` a
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
        title=definition.title,
        cells=tuple(ordered),
        missing=tuple(missing),
        filled=filled,
    )


def fill_rate(name: str, cells: Mapping[str, ParsedCell | CellValue]) -> float:
    """Filled columns / declared columns for one view (the P1 gate metric)."""

    return build_view(name, cells).fill_rate


__all__ = [
    "OVERVIEW_ONLY",
    "SPECIALIST_VIEW_NAMES",
    "VIEWS",
    "VIEW_BY_NAME",
    "VIEW_COLUMNS",
    "VIEW_NAMES",
    "ALL_COLUMNS",
    "ALL_VIEW",
    "ALL_VIEW_NAME",
    "SCREEN_VIEW_NAMES",
    "ViewDefinition",
    "ViewRow",
    "build_view",
    "columns",
    "fill_rate",
    "view",
]
