"""Shared helpers and fixtures for the Phase-4 RED suite (02 §3.7, §4.7, §5 Fase 4; 04 §2).

Not a test module (leading underscore), so pytest never collects it. Every
contract this module pins is expanded in ``briefs/market-engine/16-phase4-red.md``.

Contracts pinned here
---------------------

* **Units are parsed, never coerced.** ``app.verify.units.parse_number(raw,
  unit=...)`` returns ``float | None``: ``"N/A"``, ``"-"``, ``""`` and ``None``
  become ``None`` and **never** ``0``. ``"1,234.5"`` becomes ``1234.5``; a real
  ``"0"`` stays ``0.0``. ``to_percent(raw, field=..., unit=None)`` returns the
  display percent: ``dividend_yield=0.0031`` -> ``0.31``, ``opt_iv_atm=0.25`` ->
  ``25.0`` and ``opt_iv_atm=25`` -> ``25.0`` (values ``<= 5`` for IV are
  fractions, the 04 §1 D4 measurement). ``canonical(...)`` returns the storage
  unit: dividend yield in percent points, IV as a fraction.
* **Rules are a table, not a chain of ifs.** ``app.verify.rules.RULES`` is a
  tuple of 16 records with ``id`` (``"V1"``..``"V16"``), ``columns`` and
  ``severity``; ``severity_of(rule_id)`` answers ``critical``/``major``.
  ``evaluate(row, context=...)`` returns rejections; ``apply(row, context=...)``
  returns ``(new_row, rejections)`` and never mutates its input.
* **A rejection is an envelope.** ``{rule, column, reason, value_text}``.
  ``reason`` is a stable snake_case token (the fixture
  ``tests/fixtures/verification_rules_v1.json`` is the oracle). A failing value
  becomes ``None`` (or the documented series/flag effect of 02 §3.7) — it is
  never clamped to something that looks plausible.
* **Context is explicit, the clock is injected.** ``evaluate``/``apply`` take a
  mapping with ``now`` (ISO, aware), ``asset_class``, ``venue``, ``market_state``,
  ``session_date``, ``session_close``, ``max_staleness_s``, ``allow_delayed``,
  ``multi_class``, ``quote_currency``, ``risk_free_rate``, ``candidates`` and
  ``index``. No rule reads the wall clock or the network.
* **Quality reconciles.** ``app.verify.quality.column_blocks(rows, *,
  columns, rejections=(), disputed=())`` returns one block per canonical column
  with ``filled, verified, rejected, disputed, empty, sources, min_as_of,
  max_as_of``; the column list is injected because ``verify/`` may import only
  its own modules (``tests/_import_rules.py``). ``filled + empty == row_count``
  and ``verified + rejected + disputed == filled``; ``reconcile`` raises
  ``QualityError`` when a report does not; ``verdict`` answers
  ``PASS``/``DEGRADED``/``REJECT`` (a critical rejection, or a failed
  reconciliation, is ``REJECT``).
* **Persistence.** ``SqliteJobRepository.record_rejections(job_id, rejections)``
  writes ``verification_rejections`` rows (``job_id``, ``instrument_id``,
  ``field``/``column``, ``rule``, ``detail``/``reason``) and replaces them on a
  retry instead of stacking duplicates.
* **Wiring.** ``run_phases`` runs the verifier in P6, returns ``rejections`` and
  ``verdict`` alongside ``rows``/``phase_reports``, reports ``verify`` as ``ok``
  (not ``partial``), and refuses to export under ``REJECT``. ``process_job``
  persists the rejections, folds the quality blocks into ``usage`` and fails the
  job on ``REJECT``.

The column names in the rejection *table* are frozen by 02 §4.7 as ``field`` and
``detail``; the verifier's own envelope says ``column`` and ``reason``. The
helpers below accept either spelling and never invent a third.
"""

from __future__ import annotations

import copy
import importlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from app.contracts import CANONICAL_COLUMNS_V1, DERIVED_QUALITY_COLUMNS
from app.enrich.quality import finalize_row

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "verification_rules_v1.json"

#: Fixed clock for the pure-rule fixtures.
NOW = "2026-10-22T15:00:00+00:00"
QUOTE_AS_OF = "2026-10-22T14:59:30+00:00"
INSTRUMENT_ID = "XNAS:TEST"

#: Strings that mean "the provider has no number". None of them may become 0.
SENTINELS: tuple[Any, ...] = ("N/A", "n/a", "-", "", "   ", None, "--", "nan", "NaN")

#: Rules a critical rejection blocks the export with (04 §2, 02 §5 Fase 4).
CRITICAL_RULES: frozenset[str] = frozenset({"V7", "V9", "V11", "V15", "V16"})


# ---------------------------------------------------------------------------
# Module loading with RED-shaped failures
# ---------------------------------------------------------------------------


def _load(module_name: str) -> Any:
    """Import a Phase-4 module, turning a missing module into a RED failure."""

    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:  # pragma: no cover - RED path
        raise NotImplementedError(
            f"{module_name} is not implemented yet (Fase 4 RED): {exc}"
        ) from exc


def _require(module: Any, *names: str) -> Any:
    missing = [name for name in names if not hasattr(module, name)]
    if missing:  # pragma: no cover - RED path
        raise NotImplementedError(
            f"{module.__name__} is missing " + ", ".join(missing) + " (Fase 4 RED)"
        )
    if len(names) == 1:
        return getattr(module, names[0])
    return tuple(getattr(module, name) for name in names)


def rules_module() -> Any:
    module = _load("app.verify.rules")
    _require(module, "RULES", "evaluate", "apply", "severity_of")
    return module


def units_module() -> Any:
    module = _load("app.verify.units")
    _require(module, "parse_number", "to_percent", "canonical")
    return module


def quality_module() -> Any:
    module = _load("app.verify.quality")
    _require(module, "column_blocks", "reconcile", "verdict", "QualityError")
    return module


# ---------------------------------------------------------------------------
# Rule fixture: one clean row and one explicit context
# ---------------------------------------------------------------------------


def clean_row(**overrides: Any) -> dict[str, Any]:
    """A row where all sixteen rules must pass.

    Every value is arithmetically consistent: ``change_pct`` with the price
    move, ``market_cap`` with price x shares, ``pe_ttm`` with price/eps, the
    dividend yield with ``dividend_per_share``, the option block at zero carry
    and exact parity, and the quote 30 s old against ``NOW``.
    """

    row: dict[str, Any] = {column: None for column in CANONICAL_COLUMNS_V1}
    row.update(
        {
            "instrument_id": INSTRUMENT_ID,
            "symbol": "TEST",
            "mic": "XNAS",
            "exchange": "NASDAQ",
            "name": "Test Instruments Inc.",
            "asset_class": "equity",
            "instrument_type": "common_stock",
            "currency": "USD",
            "country": "US",
            "instrument_status": "active",
            "is_primary_listing": True,
            "index_memberships": ["SPX"],
            # Quote: 100 from 90 = +11.1111%.
            "last_price": 100.0,
            "prev_close": 90.0,
            "change_abs": 10.0,
            "change_pct": 11.1111,
            "bid": 99.99,
            "ask": 100.01,
            "day_open": 95.0,
            "day_high": 101.0,
            "day_low": 94.0,
            "day_volume": 1_000_000,
            "turnover_value": 100_000_000.0,
            "quote_as_of": QUOTE_AS_OF,
            "quote_delay": "realtime",
            "market_state": "open",
            "session_date": "2026-10-22",
            # Fundamentals: 100 x 1e9 = 1e11; 100/5 = 20; 5/100 = 5%.
            "market_cap": 1.0e11,
            "shares_outstanding": 1.0e9,
            "pe_ttm": 20.0,
            "eps_ttm": 5.0,
            "dividend_yield": 5.0,
            "dividend_per_share": 5.0,
            # Options: parity holds exactly with r = 0 and K = S.
            "opt_iv_atm": 0.25,
            "opt_delta_atm": 0.5,
            "opt_gamma_atm": 0.02,
            "opt_vega_atm": 10.0,
            "opt_strike_atm": 100.0,
            "opt_call_price": 5.0,
            "opt_put_price": 5.0,
            "opt_dte": 30,
            # A real return, so a series flag has something to null.
            "ret_1d": 0.5,
        }
    )
    row.update(overrides)
    return row


def clean_context(**overrides: Any) -> dict[str, Any]:
    """The clock, session and tolerances the rules are not allowed to guess."""

    context: dict[str, Any] = {
        "now": NOW,
        "asset_class": "equity",
        "venue": "XNAS",
        "market_state": "open",
        "session_date": "2026-10-22",
        "session_close": "2026-10-22T20:00:00+00:00",
        "max_staleness_s": None,
        "allow_delayed": False,
        "multi_class": False,
        "quote_currency": "USD",
        "risk_free_rate": 0.0,
        "candidates": {},
        "index": {"weights": [50.0, 30.0, 19.0], "published_count": 3, "member_count": 3},
    }
    context.update(overrides)
    return context


def evaluate(row: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Call ``rules.evaluate`` and normalise the result to a list of dicts."""

    result = rules_module().evaluate(dict(row), context=dict(context or clean_context()))
    return [dict(item) for item in result]


def apply(row: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Call ``rules.apply``; returns ``(new_row, rejections)``, input untouched."""

    result = rules_module().apply(dict(row), context=dict(context or clean_context()))
    if not isinstance(result, tuple) or len(result) != 2:  # pragma: no cover - RED path
        raise NotImplementedError(
            "rules.apply must return (row, rejections) — a bare row hides what was rejected"
        )
    applied, rejections = result
    return dict(applied), [dict(item) for item in rejections]


def severity_of(rule: str) -> str:
    return str(rules_module().severity_of(rule))


def registered_rules() -> list[dict[str, Any]]:
    """The ``RULES`` table as plain dictionaries, in declaration order."""

    table = rules_module().RULES
    records: list[dict[str, Any]] = []
    for entry in table:
        if isinstance(entry, Mapping):
            records.append(dict(entry))
        else:
            records.append(
                {
                    "id": getattr(entry, "id", None),
                    "columns": tuple(getattr(entry, "columns", ()) or ()),
                    "severity": getattr(entry, "severity", None),
                }
            )
    return records


# ---------------------------------------------------------------------------
# Rejection lookups
# ---------------------------------------------------------------------------


def column_of(record: Mapping[str, Any]) -> str | None:
    """The column a stored rejection names, under either frozen spelling."""

    for key in ("column", "field"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def reason_of(record: Mapping[str, Any]) -> str | None:
    """The reason a stored rejection carries, under either frozen spelling."""

    for key in ("reason", "detail"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.split(":", 1)[0].strip()
    return None


def rejection_for(
    rejections: Iterable[Mapping[str, Any]], rule: str, column: str
) -> dict[str, Any]:
    matches = [
        dict(item)
        for item in rejections
        if str(item.get("rule")) == rule and column_of(item) == column
    ]
    assert matches, (
        f"no rejection for rule={rule} column={column}; got "
        f"{[(item.get('rule'), column_of(item)) for item in rejections]}"
    )
    return matches[0]


def assert_rejection_shape(record: Mapping[str, Any], *, rule: str, column: str, reason: str) -> None:
    assert str(record.get("rule")) == rule, record
    assert column_of(record) == column, record
    assert reason_of(record) == reason, (
        f"rejection reason must be {reason!r}; got {record.get('reason')!r}/{record.get('detail')!r}"
    )


# ---------------------------------------------------------------------------
# The 16-rule table: one passing and one failing fixture per case
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleCase:
    rule: str
    column: str
    reason: str
    fixture: Callable[[bool], tuple[dict[str, Any], dict[str, Any]]]
    verify: Callable[[Mapping[str, Any]], None] | None = None

    @property
    def label(self) -> str:
        return f"{self.rule}-{self.reason}"


def _case(
    rule: str,
    column: str,
    reason: str,
    *,
    fail: Callable[[dict[str, Any]], None] | None = None,
    context: Mapping[str, Any] | None = None,
    fail_context: Mapping[str, Any] | None = None,
    verify: Callable[[Mapping[str, Any]], None] | None = None,
) -> RuleCase:
    def fixture(fails: bool) -> tuple[dict[str, Any], dict[str, Any]]:
        row = clean_row()
        ctx = clean_context()
        if context:
            ctx.update(context)
        if fails:
            if fail is not None:
                fail(row)
            if fail_context:
                ctx.update(fail_context)
        return row, ctx

    return RuleCase(rule=rule, column=column, reason=reason, fixture=fixture, verify=verify)


# -- V1: change_pct is derived, so a quoted 0 next to a real move is a lie -----


def _v1_move(row: dict[str, Any]) -> None:
    row["change_pct"] = 0.0


# -- V2 / V3: bar series -------------------------------------------------------


def _bars(row: dict[str, Any]) -> None:
    row["bars"] = [
        {"date": "2026-10-21", "open": 95.0, "high": 99.0, "low": 94.0, "close": 98.0},
        {"date": "2026-10-22", "open": 98.0, "high": 101.0, "low": 97.0, "close": 100.0},
    ]
    row["bars_count"] = 2
    row["first_bar_date"] = "2026-10-21"
    row["last_bar_date"] = "2026-10-22"


def _v2_bad_bar(row: dict[str, Any]) -> None:
    _bars(row)
    row["bars"].append(
        {"date": "2026-10-23", "open": 100.0, "high": 99.0, "low": 95.0, "close": 98.0}
    )
    row["bars_count"] = 3


def _v3_duplicate_session(row: dict[str, Any]) -> None:
    _bars(row)
    row["bars"].append(
        {"date": "2026-10-22", "open": 99.0, "high": 102.0, "low": 98.0, "close": 101.0}
    )
    row["bars_count"] = 3


def _verify_bad_bar_dropped(applied: Mapping[str, Any]) -> None:
    bars = list(applied.get("bars") or [])
    assert len(bars) == 2, f"the offending bar must be dropped, not kept: {bars}"
    for bar in bars:
        assert bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"], bar
    assert applied["bars_count"] == len(bars) == 2, applied.get("bars_count")
    assert applied["last_bar_date"] == "2026-10-22"


def _verify_duplicate_dropped(applied: Mapping[str, Any]) -> None:
    bars = list(applied.get("bars") or [])
    dates = [bar["date"] for bar in bars]
    assert dates == ["2026-10-21", "2026-10-22"], dates
    assert len(dates) == len(set(dates)), dates
    assert applied["bars_count"] == len(bars) == 2


# -- V4..V6: fundamentals ------------------------------------------------------


def _v4_cap_off(row: dict[str, Any]) -> None:
    row["market_cap"] = 1.03e11  # +3.0% against price x shares


def _v5_pe_off(row: dict[str, Any]) -> None:
    row["pe_ttm"] = 22.0  # +10% against 100/5


def _v6_yield_over_hundred(row: dict[str, Any]) -> None:
    row["dividend_yield"] = 500.0


def _v6_yield_inconsistent(row: dict[str, Any]) -> None:
    # 1.0 per share on a 100.0 price is 1.0%, not the quoted 5.0%.
    row["dividend_per_share"] = 1.0


# -- V7 / V8 / V9: the option block -------------------------------------------


def _v7_iv_over_five(row: dict[str, Any]) -> None:
    # 600% reads as a 6.0 fraction — outside (0, 5] even after normalisation.
    row["opt_iv_atm"] = 600.0


def _v8_delta_above_one(row: dict[str, Any]) -> None:
    row["opt_delta_atm"] = 1.2


def _v9_parity_broken(row: dict[str, Any]) -> None:
    row["opt_call_price"] = 6.0  # |6 - 5 - 0| = 1.0 against a 0.30 tolerance


def _verify_iv_and_greeks_cleared(applied: Mapping[str, Any]) -> None:
    cleared = ("opt_iv_atm", "opt_delta_atm", "opt_gamma_atm", "opt_theta_atm", "opt_vega_atm")
    filled = {column: applied.get(column) for column in cleared if applied.get(column) is not None}
    assert not filled, f"greeks follow the IV out (02 §3.7 V7): {filled}"
    assert applied.get("opt_strike_atm") == 100.0, "a bad IV does not invalidate the strike"


def _verify_option_block_cleared(applied: Mapping[str, Any]) -> None:
    option_columns = [
        column
        for column in CANONICAL_COLUMNS_V1
        if column.startswith("opt_") and column not in DERIVED_QUALITY_COLUMNS
    ]
    filled = {column: applied.get(column) for column in option_columns if applied.get(column) is not None}
    assert not filled, f"a suspected chain must clear the whole option block: {filled}"


# -- V10 / V11 / V12: structure, currency, splits -----------------------------


def _v10_weights_off(row: dict[str, Any]) -> None:
    row["index_memberships"] = ["SPX"]


def _v11_quote_currency_mismatch(row: dict[str, Any]) -> None:
    row["quote_currency"] = "EUR"


def _v12_split_inconsistent(row: dict[str, Any]) -> None:
    # A 2:1 split implies close_ratio 0.5; the quoted ratio is 0.8.
    row["split_ratio"] = 2.0
    row["prev_close"] = 200.0
    row["last_price"] = 160.0
    row["change_pct"] = -20.0
    row["ret_1d"] = -20.0


def _verify_partial_membership(applied: Mapping[str, Any]) -> None:
    assert applied.get("index_memberships_status") == "partial", applied.get("index_memberships_status")
    assert applied.get("index_memberships"), "membership must be marked partial, never truncated"


def _verify_unadjusted(applied: Mapping[str, Any]) -> None:
    assert applied.get("adjustment_status") == "unadjusted", applied.get("adjustment_status")
    assert applied.get("ret_1d") is None, "returns on an unadjusted series must be nulled"


# -- V13 / V14 / V15 / V16: freshness, divergence, bands, the clock -----------


def _v13_stale_in_session(row: dict[str, Any]) -> None:
    row["quote_as_of"] = "2026-10-22T14:55:00+00:00"  # 300 s old, in session


def _v14_divergence(row: dict[str, Any]) -> None:
    row["last_price"] = 100.0


def _v15_negative_volume(row: dict[str, Any]) -> None:
    row["day_volume"] = -1


def _v15_volume_unit_anomaly(row: dict[str, Any]) -> None:
    row["day_volume"] = 2_000_000_000_000  # 2e12 > 1e12


def _v16_future_as_of(row: dict[str, Any]) -> None:
    row["quote_as_of"] = "2099-01-01T00:00:00+00:00"


def _verify_stale_flag(applied: Mapping[str, Any]) -> None:
    assert applied.get("quote_status") == "stale", applied.get("quote_status")
    assert applied.get("quote_as_of"), "a stale price is a real price; the timestamp must survive"


def _verify_disputed(applied: Mapping[str, Any]) -> None:
    assert applied.get("last_price") == 100.0, "the highest-trust value must win"
    assert applied.get("disputed") is True, "a divergence marks the cell disputed"


RULE_CASES: tuple[RuleCase, ...] = (
    _case("V1", "change_pct", "change_pct_mismatch", fail=_v1_move),
    _case("V2", "day_high", "ohlc_order", fail=_v2_bad_bar, verify=_verify_bad_bar_dropped),
    _case("V3", "last_bar_date", "bar_dates_not_strict", fail=_v3_duplicate_session, verify=_verify_duplicate_dropped),
    _case("V4", "market_cap", "market_cap_mismatch", fail=_v4_cap_off),
    _case("V5", "pe_ttm", "pe_mismatch", fail=_v5_pe_off),
    _case("V6", "dividend_yield", "dividend_yield_unit", fail=_v6_yield_over_hundred),
    _case("V6", "dividend_yield", "dividend_yield_inconsistent", fail=_v6_yield_inconsistent),
    _case("V7", "opt_iv_atm", "iv_unit", fail=_v7_iv_over_five, verify=_verify_iv_and_greeks_cleared),
    _case("V8", "opt_delta_atm", "greek_sign", fail=_v8_delta_above_one),
    _case("V9", "opt_iv_atm", "parity_violation", fail=_v9_parity_broken, verify=_verify_option_block_cleared),
    _case(
        "V10",
        "index_memberships",
        "index_weight_sum",
        fail=_v10_weights_off,
        fail_context={"index": {"weights": [50.0, 30.0, 17.0], "published_count": 3, "member_count": 3}},
        verify=_verify_partial_membership,
    ),
    _case("V11", "last_price", "currency_mismatch", fail=_v11_quote_currency_mismatch),
    _case("V12", "ret_1d", "split_inconsistent", fail=_v12_split_inconsistent, verify=_verify_unadjusted),
    _case("V13", "quote_as_of", "stale_quote", fail=_v13_stale_in_session, verify=_verify_stale_flag),
    _case(
        "V14",
        "last_price",
        "provider_divergence",
        fail=_v14_divergence,
        fail_context={
            "candidates": {
                "last_price": [
                    {"source": "yahoo", "value": 100.0, "trust": 1, "as_of": QUOTE_AS_OF},
                    {"source": "scrape", "value": 103.0, "trust": 2, "as_of": QUOTE_AS_OF},
                ]
            }
        },
        verify=_verify_disputed,
    ),
    _case("V15", "day_volume", "nonpositive", fail=_v15_negative_volume),
    _case("V15", "day_volume", "unit_anomaly", fail=_v15_volume_unit_anomaly),
    _case("V16", "quote_as_of", "future_as_of", fail=_v16_future_as_of),
)


def apply_case(case: RuleCase) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    row, context = case.fixture(True)
    return apply(row, context)


def fixture_table() -> dict[str, dict[str, Any]]:
    """The JSON oracle: ``rule -> {severity, column, reasons}``."""

    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return {str(entry["id"]): dict(entry) for entry in payload["rules"]}


def new_row(**overrides: Any) -> dict[str, Any]:
    """Alias used by the quality tests: a consistent, fully populated row."""

    return clean_row(**overrides)


# ---------------------------------------------------------------------------
# Quality helpers
# ---------------------------------------------------------------------------


def column_blocks(
    rows: Sequence[Mapping[str, Any]],
    *,
    rejections: Sequence[Mapping[str, Any]] = (),
    disputed: Sequence[Mapping[str, Any]] = (),
    columns: Sequence[str] | None = None,
) -> dict[str, dict[str, Any]]:
    # ``verify/`` is import-pure (tests/_import_rules.py), so the 92-column
    # contract is injected by the caller instead of imported by the module.
    return quality_module().column_blocks(
        [dict(row) for row in rows],
        columns=tuple(columns or CANONICAL_COLUMNS_V1),
        rejections=[dict(item) for item in rejections],
        disputed=[dict(item) for item in disputed],
    )


def reconcile(
    blocks: Mapping[str, Mapping[str, Any]],
    *,
    row_count: int,
    columns: Sequence[str] | None = None,
) -> Any:
    return quality_module().reconcile(
        blocks, row_count=row_count, columns=tuple(columns or CANONICAL_COLUMNS_V1)
    )


def verdict(
    blocks: Mapping[str, Mapping[str, Any]],
    *,
    rejections: Sequence[Mapping[str, Any]] = (),
    reconciled: bool = True,
) -> str:
    return str(
        quality_module().verdict(
            blocks, rejections=[dict(item) for item in rejections], reconciled=reconciled
        )
    )


# ---------------------------------------------------------------------------
# Persistence and job wiring
# ---------------------------------------------------------------------------


def job_params(symbols: Sequence[str], **overrides: Any) -> dict[str, Any]:
    """A valid ``MarketRequest`` document for a watchlist quote job."""

    from app.contracts import MarketRequest
    from tests._phase2 import enrich_body

    params = enrich_body(symbols, **overrides)
    MarketRequest.model_validate(params)  # fail here, not inside the worker
    return params


async def create_job(repository: Any, symbols: Sequence[str], **overrides: Any) -> dict[str, Any]:
    from tests._phase3 import CLIENT_KEY

    return await repository.create(
        params=job_params(symbols, **overrides),
        api_key=CLIENT_KEY,
        rows_target=len(symbols),
        retention_days=7,
    )


async def record_rejections(
    repository: Any, job_id: str, rejections: Sequence[Mapping[str, Any]]
) -> Any:
    method = getattr(repository, "record_rejections", None)
    if method is None:  # pragma: no cover - RED path
        raise NotImplementedError(
            "SqliteJobRepository.record_rejections(job_id, rejections) is missing "
            "(02 §4.7, Fase 4)"
        )
    return await method(job_id, [dict(item) for item in rejections])


def rejection_rows(db_path: str | Path) -> list[dict[str, Any]]:
    """Read ``verification_rejections`` straight from SQLite, no ORM."""

    path = Path(db_path)
    if not path.exists():  # pragma: no cover - a job test always creates it
        return []
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "verification_rejections" not in tables:  # pragma: no cover - frozen schema
            return []
        return [
            dict(row)
            for row in connection.execute("SELECT * FROM verification_rejections ORDER BY id")
        ]


def recent_quote_as_of(seconds: int = 30) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def quote_row(symbol: str = "TEST", *, mic: str = "XNAS", currency: str = "USD", **cells: Any) -> dict[str, Any]:
    """A canonical, quote-only row that passes every rule the pipeline runs."""

    stamp = recent_quote_as_of()
    row: dict[str, Any] = {column: None for column in CANONICAL_COLUMNS_V1}
    row.update(
        {
            "instrument_id": f"{mic}:{symbol}",
            "symbol": symbol,
            "mic": mic,
            "exchange": "NASDAQ" if mic == "XNAS" else mic,
            "name": f"{symbol} Test",
            "asset_class": "equity",
            "instrument_type": "common_stock",
            "currency": currency,
            "country": "US",
            "instrument_status": "active",
            "last_price": 100.0,
            "prev_close": 90.0,
            "change_abs": 10.0,
            "change_pct": 11.1111,
            "bid": 99.99,
            "ask": 100.01,
            "day_open": 95.0,
            "day_high": 101.0,
            "day_low": 94.0,
            "day_volume": 1_000_000,
            "turnover_value": 100_000_000.0,
            "quote_as_of": stamp,
            "quote_delay": "realtime",
            "market_state": "open",
            "session_date": datetime.now(UTC).date().isoformat(),
            "primary_source": "fake",
            "as_of_achieved": stamp,
        }
    )
    row.update(cells)
    return row


def install_fetch_stub(monkeypatch: Any, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Replace the fake fetch path with a fixed set of canonical rows.

    The stub is installed on ``app.worker.runner._fetch_fake`` — the module
    attribute ``_run`` resolves — so ``process_job`` keeps its single pipeline.
    """

    runner = _load("app.worker.runner")
    outcome_class = getattr(runner, "_Outcome", None)
    if outcome_class is None:  # pragma: no cover - RED path
        raise NotImplementedError("app.worker.runner._Outcome is missing (Fase 4 RED)")
    prepared = [finalize_row(dict(row)) for row in rows]

    async def _fetch(request: Any, symbols: Sequence[str], fields: set[str]) -> Any:
        return outcome_class(rows=[copy.deepcopy(row) for row in prepared], observations={})

    monkeypatch.setattr(runner, "_fetch_fake", _fetch, raising=True)
    return prepared


# -- run_phases / usage readers -----------------------------------------------


def run_phases_fn() -> Callable[..., Any]:
    module = _load("app.enrich.orchestrator")
    return _require(module, "run_phases")


def default_phases() -> tuple[Any, ...]:
    module = _load("app.enrich.phases")
    return tuple(_require(module, "DEFAULT_PHASES"))


def rows_from(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, Mapping):
        rows = result.get("rows")
    else:
        rows = getattr(result, "rows", None)
    if rows is None:  # pragma: no cover - RED path
        raise NotImplementedError("run_phases must return 'rows' (Fase 4 RED)")
    return [dict(row) for row in rows]


def phase_reports(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, Mapping):
        reports = result.get("phase_reports")
    else:
        reports = getattr(result, "phase_reports", None)
    if not reports:  # pragma: no cover - RED path
        raise NotImplementedError("run_phases must return 'phase_reports' (Fase 4 RED)")
    return [dict(report) for report in reports]


def report_named(reports: Sequence[Mapping[str, Any]], name: str) -> dict[str, Any]:
    for report in reports:
        if report.get("name") == name:
            return dict(report)
    raise AssertionError(f"no phase report named {name!r}; got {[r.get('name') for r in reports]}")


def result_key(result: Any, key: str) -> Any:
    """Read a Phase-4 key from the ``run_phases`` result, or fail as RED."""

    if isinstance(result, Mapping):
        if key in result:
            return result[key]
    elif hasattr(result, key):
        return getattr(result, key)
    raise NotImplementedError(
        f"run_phases must return {key!r} so the verifier's verdict and rejections "
        f"reach the worker and the audit table (Fase 4 RED)"
    )


def result_rejections(result: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in result_key(result, "rejections")]


def result_verdict(result: Any) -> str:
    return str(result_key(result, "verdict"))


def usage_verification(usage: Mapping[str, Any]) -> dict[str, Any]:
    block = usage.get("verification")
    if not isinstance(block, Mapping):  # pragma: no cover - RED path
        raise NotImplementedError(
            "usage must carry a 'verification' block with the verdict and rejection "
            "counts; a job view without it cannot tell PASS from REJECT (Fase 4 RED)"
        )
    return dict(block)


def phase_usage(usage: Mapping[str, Any], name: str) -> dict[str, Any]:
    phases = usage.get("phases")
    assert isinstance(phases, Mapping) and name in phases, (
        f"usage['phases'][{name!r}] is missing; got {sorted(phases or {})}"
    )
    return dict(phases[name])


__all__ = [
    "CRITICAL_RULES",
    "FIXTURE_PATH",
    "INSTRUMENT_ID",
    "NOW",
    "QUOTE_AS_OF",
    "RULE_CASES",
    "RuleCase",
    "SENTINELS",
    "apply",
    "apply_case",
    "assert_rejection_shape",
    "clean_context",
    "clean_row",
    "column_blocks",
    "column_of",
    "default_phases",
    "evaluate",
    "fixture_table",
    "install_fetch_stub",
    "new_row",
    "phase_reports",
    "phase_usage",
    "quality_module",
    "quote_row",
    "reason_of",
    "recent_quote_as_of",
    "record_rejections",
    "reconcile",
    "registered_rules",
    "rejection_for",
    "rejection_rows",
    "report_named",
    "result_key",
    "result_rejections",
    "result_verdict",
    "rows_from",
    "rules_module",
    "run_phases_fn",
    "severity_of",
    "units_module",
    "usage_verification",
    "verdict",
]
