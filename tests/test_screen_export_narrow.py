"""``POST /screens/export`` — narrow support.

The narrow is an optional body field the dialog sends:
  * ``narrow`` — an object with ``filters`` (WireFilter[]) and optional ``signal``
  * ``signal`` — a top-level string for performance narrow in criteria mode

These tests prove parse_export accepts both shapes without breaking the
existing contract: every old knob must still work, and the extra keys are
never required.
"""

from __future__ import annotations

import pytest

from app.screen.export_direct import MAX_ROWS, ExportError, FORMATS, parse_export


# ---------------------------------------------------------------------------
# Existing contract: every old knob still works
# ---------------------------------------------------------------------------


def test_criteria_mode_still_works_unchanged() -> None:
    spec = parse_export(
        {
            "mode": "criteria",
            "format": "csv",
            "limit": 42,
            "criteria": {"filters": [{"key": "cap", "op": "gte", "value": 1e9}]},
        }
    )
    assert spec.mode == "criteria"
    assert spec.format == "csv"
    assert spec.limit == 42
    assert spec.criteria is not None
    assert spec.criteria["filters"] == [{"key": "cap", "op": "gte", "value": 1e9}]


def test_symbols_mode_still_works_unchanged() -> None:
    spec = parse_export(
        {
            "mode": "symbols",
            "format": "xlsx",
            "symbols": ["AAPL", "MSFT"],
        }
    )
    assert spec.mode == "symbols"
    assert spec.format == "xlsx"
    assert spec.limit == MAX_ROWS
    assert spec.symbols == ("AAPL", "MSFT")


# ---------------------------------------------------------------------------
# Criteria mode with signal
# ---------------------------------------------------------------------------


def test_criteria_mode_accepts_top_level_signal() -> None:
    """The client sends `signal` alongside criteria when performance narrow is set."""
    spec = parse_export(
        {
            "mode": "criteria",
            "format": "csv",
            "criteria": {"filters": [{"key": "ta_beta", "op": "lt", "value": 0.8}]},
            "signal": "gainers",
        }
    )
    assert spec.signal == "gainers"
    assert spec.criteria is not None


def test_criteria_mode_signal_is_optional() -> None:
    """Omitting signal is still valid."""
    spec = parse_export(
        {
            "mode": "criteria",
            "format": "csv",
            "criteria": {"filters": []},
        }
    )
    assert spec.signal is None


# ---------------------------------------------------------------------------
# Symbols mode with narrow
# ---------------------------------------------------------------------------


def test_symbols_mode_accepts_narrow_block() -> None:
    spec = parse_export(
        {
            "mode": "symbols",
            "format": "csv",
            "symbols": ["AAPL", "MSFT", "NVDA"],
            "narrow": {
                "filters": [
                    {"key": "cap", "op": "gte", "value": 2e9},
                    {"key": "cap", "op": "lt", "value": 10e9},
                    {"key": "sec", "op": "eq", "value": "Finance"},
                ],
            },
        }
    )
    assert spec.symbols == ("AAPL", "MSFT", "NVDA")
    assert spec.narrow is not None
    assert spec.narrow.filters == (
                {"key": "cap", "op": "gte", "value": 2e9},
                {"key": "cap", "op": "lt", "value": 10e9},
                {"key": "sec", "op": "eq", "value": "Finance"},
            )
    assert spec.narrow.signal is None


def test_symbols_mode_narrow_with_signal() -> None:
    spec = parse_export(
        {
            "mode": "symbols",
            "format": "csv",
            "symbols": ["AAPL"],
            "narrow": {
                "filters": [{"key": "sh_curvol", "op": "gte", "value": 10_000_000}],
                "signal": "gainers",
            },
        }
    )
    assert spec.narrow is not None
    assert spec.narrow.signal == "gainers"


def test_symbols_mode_narrow_is_optional() -> None:
    spec = parse_export(
        {
            "mode": "symbols",
            "format": "json",
            "symbols": ["AAPL"],
        }
    )
    assert spec.narrow is None


# ---------------------------------------------------------------------------
# Narrow validation
# ---------------------------------------------------------------------------


def test_narrow_without_filters_is_refused() -> None:
    with pytest.raises(ExportError) as excinfo:
        parse_export(
            {
                "mode": "symbols",
                "format": "csv",
                "symbols": ["AAPL"],
                "narrow": {"signal": "gainers"},
            }
        )
    assert "narrow" in str(excinfo.value).lower()


def test_narrow_filters_is_empty_ok() -> None:
    """An empty filters array is fine — the reader chose no narrowing."""
    spec = parse_export(
        {
            "mode": "symbols",
            "format": "csv",
            "symbols": ["AAPL"],
            "narrow": {"filters": []},
        }
    )
    assert spec.narrow is not None
    assert spec.narrow.filters == ()


def test_narrow_refused_for_criteria_mode() -> None:
    """narrow is for symbols mode; criteria mode uses signal at top level."""
    with pytest.raises(ExportError) as excinfo:
        parse_export(
            {
                "mode": "criteria",
                "format": "csv",
                "criteria": {"filters": []},
                "narrow": {"filters": []},
            }
        )
    assert "narrow" in str(excinfo.value).lower()


def test_unknown_knob_still_refused() -> None:
    """A knob the API does not know is still a 422."""
    with pytest.raises(ExportError) as excinfo:
        parse_export(
            {
                "mode": "criteria",
                "format": "csv",
                "criteria": {"filters": []},
                "bogus": True,
            }
        )
    assert "bogus" in str(excinfo.value)